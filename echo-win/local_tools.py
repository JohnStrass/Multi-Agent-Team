#!/usr/bin/env python
# local_tools.py — Local's local, network-free tool runtime.
#
# Every capability the local fine-tuned model ("Local") can invoke lives here.
# Design rules (the operator's, 2026-06-28):
#   * MAX local power: image gen, file create/read/list, real code execution.
#   * NO internet, ever. Local has no tools of her own — she only emits text.
#     The ONLY way she could touch the network is a tool that does it for her,
#     and none here do. The one tool that runs arbitrary code (run_code) is
#     isolated in a Docker container started with --network none, so even code
#     she writes physically cannot reach the internet.
#   * File tools are jailed to a workspace dir (her "studio"); paths that try
#     to escape it are refused. This bounds blast radius from 12B path typos.
#   * New capabilities come through request_tool: she describes what she wants,
#     it's logged for the operator to vet and build. She never installs anything herself.
#
# run_tool(name, args) -> dict with keys:
#   ok       : bool
#   summary  : str   # fed back to the model as the tool result
#   artifact : dict  # optional, forwarded to the UI (e.g. an image to show)
#   error    : str   # present when ok is False

import os, json, subprocess, time, uuid, shutil, urllib.parse

WORKSPACE       = os.environ.get("ECHO_WORKSPACE", os.path.expanduser("~/echo-workspace"))
REQUESTS_LOG    = os.path.join(WORKSPACE, "_tool-requests.jsonl")
GENERATE_IMAGE  = os.environ.get("GENERATE_IMAGE", "generate_image.py")
MAX_READ_CHARS  = 20000   # cap how much file/stdout text we feed back to the model
CODE_TIMEOUT    = 60      # seconds a sandboxed run may take

os.makedirs(WORKSPACE, exist_ok=True)


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------
def _ok(summary, artifact=None):
    r = {"ok": True, "summary": summary}
    if artifact:
        r["artifact"] = artifact
    return r


def _err(msg):
    return {"ok": False, "summary": f"ERROR: {msg}", "error": msg}


def _wsfile_url(rel):
    """Same-origin URL for a workspace file (served by Echo's /api/wsfile)."""
    return "/api/wsfile?path=" + urllib.parse.quote(rel.replace("\\", "/"))


def _safe_path(relpath):
    """Resolve relpath under WORKSPACE; refuse anything that escapes it."""
    if not relpath or not isinstance(relpath, str):
        raise ValueError("a 'path' string is required")
    # Reject absolute paths / drive letters outright; everything is workspace-relative.
    candidate = os.path.normpath(os.path.join(WORKSPACE, relpath))
    full = os.path.realpath(candidate)
    ws = os.path.realpath(WORKSPACE)
    if full != ws and not full.startswith(ws + os.sep):
        raise ValueError(f"path '{relpath}' escapes the workspace; refused")
    return full


# ----------------------------------------------------------------------------
# tools
# ----------------------------------------------------------------------------
def t_paint(args):
    """Render an image from a text prompt on the 4070 via ComfyUI/SDXL."""
    prompt = (args.get("prompt") or "").strip()
    if not prompt:
        return _err("paint needs a 'prompt'")
    cmd = ["py", GENERATE_IMAGE, prompt, "--json"]
    if args.get("negative"):
        cmd += ["--negative", str(args["negative"])]
    if args.get("steps"):
        cmd += ["--steps", str(int(args["steps"]))]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return _err("image generation timed out (is ComfyUI running, 4070 free?)")
    if r.returncode != 0:
        return _err(f"paint failed: {r.stderr.strip()[:400]}")
    data = json.loads(r.stdout)
    fn = data.get("filename", "")
    url = "/api/image?filename=" + fn + "&subfolder=&type=output"
    # Persist a copy into her studio so she can build on it (e.g. images_to_video).
    saved = ""
    try:
        pdir = os.path.join(WORKSPACE, "paintings")
        os.makedirs(pdir, exist_ok=True)
        shutil.copy2(data.get("path", ""), os.path.join(pdir, fn))
        saved = f" A copy is saved in paintings/{fn}."
    except Exception:
        pass
    return _ok(
        f"Image rendered and shown to the user (file: {fn}, seed {data.get('seed')})." + saved,
        artifact={"type": "image", "url": url, "prompt": prompt},
    )


def t_speak(args):
    """Give Local a voice: synthesize speech to a .wav with offline Windows TTS."""
    text = (args.get("text") or "").strip()
    if not text:
        return _err("speak needs 'text'")
    voice = args.get("voice")  # optional: "David" or "Zira"
    out_name = f"speech_{uuid.uuid4().hex[:8]}.wav"
    out_path = os.path.join(WORKSPACE, out_name)
    txt_path = os.path.join(WORKSPACE, f"._speak_{uuid.uuid4().hex[:8]}.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(text)
    voice_line = (f"$s.SelectVoice('Microsoft {voice} Desktop');"
                  if voice in ("David", "Zira") else "")
    ps = ("Add-Type -AssemblyName System.Speech;"
          "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
          + voice_line +
          f"$t = Get-Content -Raw -Encoding UTF8 '{txt_path}';"
          f"$s.SetOutputToWaveFile('{out_path}');"
          "$s.Speak($t); $s.Dispose()")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                            capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return _err("speech synthesis timed out")
    finally:
        try:
            os.remove(txt_path)
        except OSError:
            pass
    if r.returncode != 0 or not os.path.isfile(out_path):
        return _err(f"speak failed: {(r.stderr or '').strip()[:300]}")
    return _ok(f"Spoke {len(text)} characters aloud (saved as {out_name}).",
               artifact={"type": "audio", "url": _wsfile_url(out_name)})


def t_images_to_video(args):
    """Stitch workspace images into a slideshow video (offline, via ffmpeg)."""
    images = args.get("images")
    if not images or not isinstance(images, list):
        return _err("images_to_video needs 'images': a list of workspace image paths")
    try:
        seconds = float(args.get("seconds_each", 2))
    except (TypeError, ValueError):
        seconds = 2.0
    out_name = args.get("output") or f"video_{uuid.uuid4().hex[:8]}.mp4"
    try:
        out_path = _safe_path(out_name)
        frames = []
        for rel in images:
            p = _safe_path(rel)
            if not os.path.isfile(p):
                return _err(f"no such image in workspace: {rel}")
            frames.append(p)
    except ValueError as e:
        return _err(str(e))

    list_path = os.path.join(WORKSPACE, f"._frames_{uuid.uuid4().hex[:8]}.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        for p in frames:
            f.write(f"file '{p.replace(chr(92), '/')}'\nduration {seconds}\n")
        f.write(f"file '{frames[-1].replace(chr(92), '/')}'\n")  # hold last frame
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
           "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p", "-r", "30",
           out_path]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return _err("ffmpeg timed out")
    except FileNotFoundError:
        return _err("ffmpeg not found on PATH")
    finally:
        try:
            os.remove(list_path)
        except OSError:
            pass
    if r.returncode != 0 or not os.path.isfile(out_path):
        return _err(f"ffmpeg failed: {(r.stderr or '').strip()[-400:]}")
    return _ok(f"Made a {len(frames)}-image video ({out_name}).",
               artifact={"type": "video", "url": _wsfile_url(out_name)})


def t_compose_music(args):
    """Compose a short melody and render it to audio (pure offline synthesis)."""
    notes = (args.get("notes") or "").strip()
    if not notes:
        return _err("compose_music needs 'notes' like 'C4:1 E4:1 G4:2 R:0.5 C5:2' "
                    "(NOTE:BEATS tokens; R = rest)")
    try:
        tempo = float(args.get("tempo", 120))
    except (TypeError, ValueError):
        tempo = 120.0
    out_name = args.get("output") or f"music_{uuid.uuid4().hex[:8]}.wav"
    try:
        out_path = _safe_path(out_name)
    except ValueError as e:
        return _err(str(e))
    try:
        import music_synth
        music_synth.synth_melody(notes, out_path, tempo=tempo)
    except Exception as e:
        return _err(f"compose_music failed: {e}")
    return _ok(f"Composed a melody ({out_name}).",
               artifact={"type": "audio", "url": _wsfile_url(out_name)})


def t_write_file(args):
    """Create/overwrite a text file inside the workspace."""
    try:
        full = _safe_path(args.get("path"))
    except ValueError as e:
        return _err(str(e))
    content = args.get("content", "")
    if not isinstance(content, str):
        content = json.dumps(content, indent=2)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)
    rel = os.path.relpath(full, WORKSPACE)
    return _ok(f"Wrote {len(content)} chars to {rel}.")


def t_read_file(args):
    """Read a text file from the workspace."""
    try:
        full = _safe_path(args.get("path"))
    except ValueError as e:
        return _err(str(e))
    if not os.path.isfile(full):
        return _err(f"no such file: {args.get('path')}")
    with open(full, "r", encoding="utf-8", errors="replace") as f:
        text = f.read(MAX_READ_CHARS + 1)
    truncated = len(text) > MAX_READ_CHARS
    text = text[:MAX_READ_CHARS]
    rel = os.path.relpath(full, WORKSPACE)
    note = "  [truncated]" if truncated else ""
    return _ok(f"Contents of {rel}{note}:\n{text}")


def t_list_dir(args):
    """List a directory inside the workspace (default: workspace root)."""
    try:
        full = _safe_path(args.get("path") or ".")
    except ValueError as e:
        return _err(str(e))
    if not os.path.isdir(full):
        return _err(f"not a directory: {args.get('path')}")
    entries = []
    for name in sorted(os.listdir(full)):
        p = os.path.join(full, name)
        kind = "dir " if os.path.isdir(p) else "file"
        size = os.path.getsize(p) if os.path.isfile(p) else ""
        entries.append(f"  [{kind}] {name} {size}")
    rel = os.path.relpath(full, WORKSPACE)
    listing = "\n".join(entries) if entries else "  (empty)"
    return _ok(f"{rel}/\n{listing}")


def t_request_tool(args):
    """Local's channel to ask the operator for a NEW capability. Logged, never auto-built."""
    name = (args.get("name") or "").strip()
    desc = (args.get("description") or "").strip()
    why = (args.get("why") or "").strip()
    if not name or not desc:
        return _err("request_tool needs a 'name' and a 'description'")
    rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
           "name": name, "description": desc, "why": why}
    with open(REQUESTS_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return _ok(
        f"Logged your request for a '{name}' tool. the operator will review it and, if "
        f"it's safe, build it for you. It is NOT available yet.")


def t_run_code(args):
    """Run code in a network-isolated Docker container (--network none).
    Arbitrary code is allowed BUT has no internet and only sees the workspace."""
    lang = (args.get("language") or "python").lower()
    code = args.get("code") or ""
    if not code.strip():
        return _err("run_code needs 'code'")
    if lang in ("py", "python"):
        image, ext, run = "python:3.12-slim", "py", ["python"]
    elif lang in ("js", "javascript", "node"):
        image, ext, run = "node:22-slim", "js", ["node"]
    else:
        return _err(f"unsupported language '{lang}' (use python or javascript)")

    # Stage the script inside the workspace so the container can mount it and
    # any files the code writes land in her studio.
    script_name = f"._local_run_{uuid.uuid4().hex[:8]}.{ext}"
    script_path = os.path.join(WORKSPACE, script_name)
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(code)
    docker_cmd = [
        "docker", "run", "--rm",
        "--network", "none",            # <-- the internet wall
        "--memory", "512m", "--cpus", "1", "--pids-limit", "256",
        "-v", f"{WORKSPACE}:/work", "-w", "/work",
        image, *run, f"/work/{script_name}",
    ]
    try:
        r = subprocess.run(docker_cmd, capture_output=True, text=True,
                            timeout=CODE_TIMEOUT)
    except subprocess.TimeoutExpired:
        return _err(f"code run exceeded {CODE_TIMEOUT}s and was killed")
    except FileNotFoundError:
        return _err("docker not found / engine not running — code sandbox unavailable")
    finally:
        try:
            os.remove(script_path)
        except OSError:
            pass

    out = (r.stdout or "")[:MAX_READ_CHARS]
    errtxt = (r.stderr or "")[:4000]
    if r.returncode != 0:
        # A non-zero exit is still a normal result to report back to her.
        return _ok(f"[exit {r.returncode}] (no internet; workspace only)\n"
                   f"stdout:\n{out}\nstderr:\n{errtxt}")
    return _ok(f"stdout:\n{out}" + (f"\nstderr:\n{errtxt}" if errtxt.strip() else ""))


# name -> (function, one-line description for the model)
TOOLS = {
    "paint":           (t_paint,           "Render an image from a text prompt; it is shown to the user and saved in paintings/. args: prompt, [negative], [steps]"),
    "speak":           (t_speak,           "Say something aloud in your own voice (offline text-to-speech). args: text, [voice: David|Zira]"),
    "images_to_video": (t_images_to_video, "Stitch your workspace images into a slideshow video. args: images (list of paths), [seconds_each], [output]"),
    "compose_music":    (t_compose_music,   "Compose a melody and render it to audio. args: notes (e.g. 'C4:1 E4:1 G4:2 R:0.5 C5:2', R=rest), [tempo], [output]"),
    "write_file":      (t_write_file,      "Create/overwrite a text file in your workspace. args: path, content"),
    "read_file":       (t_read_file,       "Read a text file from your workspace. args: path"),
    "list_dir":        (t_list_dir,        "List a folder in your workspace. args: [path]"),
    "run_code":        (t_run_code,        "Run python or javascript in a sandbox with NO internet (workspace files only). args: language, code"),
    "request_tool":    (t_request_tool,    "Ask the operator for a NEW capability you don't have yet. args: name, description, why"),
}


def run_tool(name, args):
    if name not in TOOLS:
        return _err(f"unknown tool '{name}'. Available: {', '.join(TOOLS)}")
    if not isinstance(args, dict):
        return _err(f"tool '{name}' args must be an object")
    try:
        return TOOLS[name][0](args)
    except Exception as e:
        return _err(f"tool '{name}' crashed: {e}")


def tools_help():
    """The tool catalog text injected into Local's system preamble."""
    lines = [f"- {n}: {desc}" for n, (_, desc) in TOOLS.items()]
    return "\n".join(lines)


if __name__ == "__main__":
    # Quick self-test of the network-free tools (no model, no Docker needed).
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(run_tool("write_file", {"path": "hello.txt", "content": "hi from a tool"}))
    print(run_tool("list_dir", {}))
    print(run_tool("read_file", {"path": "hello.txt"}))
    print(run_tool("read_file", {"path": "../../etc/passwd"}))  # must be refused
    print(run_tool("request_tool", {"name": "play_sound",
                                     "description": "play a wav file",
                                     "why": "to celebrate finishing a piece"}))
