import http.server
import socketserver
import urllib.request
import urllib.parse
import json
import os
import re
import subprocess
import mimetypes

import local_tools

PORT = 8808
REMOTE_ECHO = "http://localhost:8800"
COMFY = "http://127.0.0.1:8188"
CLOUD_MODELS = {"deepseek", "kimi", "claude"}

# Local = the local fine-tuned model, served on the mini PC's Ollama. Echo drives
# her here and runs her tool calls locally on this (Windows) rig.
LOCAL_LABEL  = "Local (tools)"
LOCAL_MODEL  = os.environ.get("LOCAL_MODEL", "gemma4-12b-local:latest")
LOCAL_OLLAMA = os.environ.get("LOCAL_OLLAMA", "http://localhost:11434")
LOCAL_MAX_STEPS = 6   # cap tool-call rounds so a loop can't run away

# Q6 "best quality" mode: drives the higher-fidelity Q6 fine-tune served by
# LM Studio on this rig (OpenAI-compatible API). Tradeoff: the Q6 (~9.5GB) and
# ComfyUI both want the 4070, so before a paint we unload LM Studio's model via
# the `lms` CLI to free VRAM; LM Studio JIT-reloads it on the next chat turn.
LOCAL_Q6_LABEL = "Local-Q6 (tools)"
LOCAL_Q6_MODEL = os.environ.get("LOCAL_Q6_MODEL", "local-finetune-gemma-4-12b-it-gguf/gemma-4-12b-it-fixedtemplate.q6_k.gguf")
LMSTUDIO_BASE = "http://127.0.0.1:1234"
LMS_BIN       = os.environ.get("LMS_BIN", os.path.expanduser("~/.lmstudio/bin/lms.exe"))

LOCAL_SYSTEM = (
    "You are running on the operator's home computer with access to a small set of REAL "
    "LOCAL tools. You have NO internet access — that is deliberate and permanent, "
    "not a limitation to apologize for or work around.\n\n"
    "CRITICAL: describing an action in prose does NOTHING. Saying 'let me paint "
    "this' or 'I'll write the file' has no effect at all. The ONLY way anything "
    "actually happens is by emitting a tool block. To paint, you must emit the "
    "paint tool — do not narrate a painting, produce one. Don't perform a "
    "capability; use it.\n\n"
    "To use a tool, output a fenced code block tagged `tool` whose body is JSON:\n"
    "```tool\n"
    '{\"name\": \"<tool>\", \"args\": { ... }}\n'
    "```\n"
    "Example — to paint a fox you would output exactly:\n"
    "```tool\n"
    '{\"name\": \"paint\", \"args\": {\"prompt\": \"a red fox in the snow, watercolor\"}}\n'
    "```\n"
    "You may emit several tool blocks in one turn. After they run you'll receive "
    "their results and can continue or call more. Only when the work is actually "
    "DONE do you reply in plain prose with no tool block.\n\n"
    "Available tools:\n" + local_tools.tools_help() + "\n\n"
    "Your file tools live in your own private workspace. run_code executes in a "
    "sandbox with no internet, seeing only that workspace. If you want a capability "
    "you don't have, use request_tool — the operator will review it and build it if it's safe."
)

# Match ANY fenced block (any tag, any case: ```tool, ```TOOL, ```tool_code,
# ```json, ``` ...). We then try to JSON-parse each block's body and keep the
# ones that look like a tool call. Per-block non-greedy capture so braces inside
# a block (e.g. run_code source) don't break parsing.
_FENCE = re.compile(r"```[a-zA-Z_]*\s*\n?(.*?)```", re.DOTALL)

def parse_tool_calls(text):
    """Extract {name, args, span} tool calls from a model reply. `span` is the
    (start, end) of the whole fenced block so the caller can strip just the
    blocks that were real tool calls (leaving ordinary code blocks intact)."""
    calls = []
    for m in _FENCE.finditer(text or ""):
        try:
            obj = json.loads(m.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict) and "name" in obj:
            calls.append({"name": obj["name"], "args": obj.get("args", {}),
                          "span": m.span()})
    return calls


def strip_tool_blocks(text, calls):
    """Remove the fenced blocks that were tool calls; keep the rest of the text."""
    for c in sorted(calls, key=lambda c: c["span"][0], reverse=True):
        s, e = c["span"]
        text = text[:s] + text[e:]
    return text.strip()


# Gemma turn-boundary markers — stop generation here so the model can't run on
# and hallucinate the next turn (the Q6 FIXEDTEMPLATE GGUF leaks these).
GEMMA_STOP = ["<end_of_turn>", "<start_of_turn>", "<endof_turn>"]


def ollama_chat(base, model, messages):
    """One non-streaming chat turn against an Ollama server; returns the text.
    Retries once on a transient connection error (the mini PC handles one
    request at a time and can briefly time out under load)."""
    body = json.dumps({"model": model, "messages": messages, "stream": False,
                       "options": {"stop": GEMMA_STOP}}).encode("utf-8")
    last_err = None
    for _attempt in range(2):
        try:
            req = urllib.request.Request(
                base + "/api/chat", method="POST",
                headers={"Content-Type": "application/json"}, data=body)
            with urllib.request.urlopen(req, timeout=300) as r:
                data = json.loads(r.read().decode("utf-8"))
            return data.get("message", {}).get("content", "")
        except urllib.error.URLError as e:
            last_err = e
    raise last_err


def openai_chat(base, model, messages):
    """One non-streaming chat turn against an OpenAI-compatible server
    (LM Studio); returns the text."""
    req = urllib.request.Request(
        base + "/v1/chat/completions",
        method="POST",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"model": model, "messages": messages, "stream": False,
                         "stop": GEMMA_STOP}).encode("utf-8"),
    )
    with urllib.request.urlopen(req, timeout=600) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data["choices"][0].get("message", {}).get("content", "")


def lms_unload():
    """Free the 4070 by unloading LM Studio's model (best-effort)."""
    try:
        subprocess.run([LMS_BIN, "unload", "--all"], capture_output=True,
                       text=True, timeout=30)
    except Exception:
        pass  # if it fails, paint may hit a VRAM error which surfaces cleanly


class EchoWinHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        try:
            if self.path == '/':
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
                self.end_headers()
                with open(os.path.join(os.path.dirname(__file__), 'index.html'), 'rb') as file:
                    self.wfile.write(file.read())
            elif self.path == '/api/models':
                response = urllib.request.urlopen('http://127.0.0.1:11434/api/tags')
                data = json.loads(response.read().decode('utf-8'))
                models = [model['name'] for model in data['models']]
                models.extend(CLOUD_MODELS)
                models.append("group")  # Add "group" to the models list
                models.append(LOCAL_LABEL)     # Local (Q4, mini PC) + tool runtime
                models.append(LOCAL_Q6_LABEL)  # Local (Q6, LM Studio) + tool runtime
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'models': models}).encode('utf-8'))
            elif self.path.startswith('/api/image?'):
                # Same-origin proxy for ComfyUI-rendered images. The browser only
                # ever talks to this server (8808), so it never needs to reach the
                # ComfyUI port directly (which fails behind sandboxes/tunnels).
                query = urllib.parse.urlparse(self.path).query
                params = urllib.parse.parse_qs(query)
                fn = params.get('filename', [''])[0]
                sub = params.get('subfolder', [''])[0]
                typ = params.get('type', ['output'])[0]
                view = (COMFY + "/view?" + urllib.parse.urlencode(
                    {'filename': fn, 'subfolder': sub, 'type': typ}))
                with urllib.request.urlopen(view, timeout=30) as upstream:
                    body = upstream.read()
                    ctype = upstream.headers.get('Content-Type', 'image/png')
                self.send_response(200)
                self.send_header('Content-Type', ctype)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path.startswith('/api/wsfile?'):
                # Serve a file from Local's workspace (audio/video/image/text she
                # made), same-origin and jailed to the workspace.
                query = urllib.parse.urlparse(self.path).query
                rel = urllib.parse.parse_qs(query).get('path', [''])[0]
                try:
                    full = local_tools._safe_path(rel)
                except ValueError:
                    self.send_error(400, "bad path"); return
                if not os.path.isfile(full):
                    self.send_error(404, "Not Found"); return
                ctype = mimetypes.guess_type(full)[0] or 'application/octet-stream'
                with open(full, 'rb') as fh:
                    body = fh.read()
                self.send_response(200)
                self.send_header('Content-Type', ctype)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_error(404, "Not Found")
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(str(e).encode('utf-8'))

    def do_POST(self):
        try:
            if self.path == '/api/chat':
                content_length = int(self.headers['Content-Length'])
                post_data = json.loads(self.rfile.read(content_length))
                model = post_data['model']
                messages = post_data['messages']

                # Proxy condition changed to include "group"
                if model in CLOUD_MODELS or model == "group":
                    request_body = {
                        "model": model,
                        "messages": messages
                    }
                    if 'members' in post_data:
                        request_body['members'] = post_data['members']

                    request = urllib.request.Request(
                        REMOTE_ECHO + "/api/chat",
                        method='POST',
                        headers={'Content-Type': 'application/json'},
                        data=json.dumps(request_body).encode('utf-8')
                    )

                    response = urllib.request.urlopen(request)
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/plain')
                    self.end_headers()
                    for line in response:
                        if line.strip():
                            self.wfile.write(line)
                            self.wfile.flush()
                else:
                    request = urllib.request.Request(
                        'http://127.0.0.1:11434/api/chat',
                        method='POST',
                        headers={'Content-Type': 'application/json'},
                        data=json.dumps({
                            "model": model,
                            "messages": messages,
                            "stream": True
                        }).encode('utf-8')
                    )

                    response = urllib.request.urlopen(request)
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/plain')
                    self.end_headers()
                    for line in response:
                        if line.strip():
                            obj = json.loads(line.decode('utf-8'))
                            if "message" in obj and "content" in obj["message"]:
                                self.wfile.write(obj["message"]["content"].encode('utf-8'))
                                self.wfile.flush()
                            elif "done" in obj and obj["done"]:
                                break
            elif self.path == '/api/paint':
                content_length = int(self.headers['Content-Length'])
                post_data = json.loads(self.rfile.read(content_length))
                prompt = post_data.get('prompt', '')

                if not prompt:
                    self.send_response(400)
                    self.send_header('Content-Type', 'text/plain')
                    self.end_headers()
                    self.wfile.write(b'Missing "prompt" field in request body.')
                    return

                try:
                    result = subprocess.run(
                        ['py', os.environ.get("GENERATE_IMAGE", "generate_image.py"), prompt, '--json'],
                        capture_output=True,
                        text=True,
                        timeout=300
                    )

                    if result.returncode == 0:
                        response_data = json.loads(result.stdout)
                        # Hand the browser a same-origin URL (proxied via /api/image)
                        # rather than the raw ComfyUI :8188 URL it may not reach.
                        response_data['view_url'] = '/api/image?' + urllib.parse.urlencode({
                            'filename': response_data.get('filename', ''),
                            'subfolder': '',
                            'type': 'output',
                        })
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/json')
                        self.end_headers()
                        self.wfile.write(json.dumps(response_data).encode('utf-8'))
                    else:
                        self.send_response(500)
                        self.send_header('Content-Type', 'text/plain')
                        self.end_headers()
                        self.wfile.write(result.stderr.encode('utf-8'))
                except subprocess.TimeoutExpired:
                    self.send_response(504)
                    self.send_header('Content-Type', 'text/plain')
                    self.end_headers()
                    self.wfile.write(b'Image generation timed out.')
            elif self.path == '/api/local':
                # Local's agent loop: drive the mini-PC model, run her tool calls
                # locally, stream events back as newline-delimited JSON.
                content_length = int(self.headers['Content-Length'])
                post_data = json.loads(self.rfile.read(content_length))
                convo = [{"role": "system", "content": LOCAL_SYSTEM}]
                convo += post_data.get("messages", [])

                # Pick the backend: Q4 on the mini PC (default) or Q6 in LM Studio.
                is_q6 = post_data.get("model") == LOCAL_Q6_LABEL
                if is_q6:
                    def chat(msgs):
                        return openai_chat(LMSTUDIO_BASE, LOCAL_Q6_MODEL, msgs)
                else:
                    def chat(msgs):
                        return ollama_chat(LOCAL_OLLAMA, LOCAL_MODEL, msgs)

                self.send_response(200)
                self.send_header('Content-Type', 'application/x-ndjson')
                self.end_headers()

                def emit(obj):
                    self.wfile.write((json.dumps(obj) + "\n").encode("utf-8"))
                    self.wfile.flush()

                # Heuristic: did the user actually ask her to DO something? If so
                # and her reply has no tool call, she likely narrated instead of
                # acting (her known failure mode) — nudge her once to act for real.
                last_user = next((m["content"] for m in reversed(post_data.get("messages", []))
                                  if m.get("role") == "user"), "").lower()
                action_words = ("paint", "draw", "paint me", "write", "save", "create",
                                "make", "generate", "run ", "list", "read", "build")
                action_intent = any(w in last_user for w in action_words)
                nudged = False
                acted = False   # has any tool actually run yet this request?

                try:
                    for _step in range(LOCAL_MAX_STEPS):
                        reply = chat(convo)
                        calls = parse_tool_calls(reply)
                        shown = strip_tool_blocks(reply, calls)
                        if shown:
                            emit({"type": "assistant", "content": shown})
                        if not calls:
                            # Only nudge if she's narrated WITHOUT ever acting.
                            if action_intent and not nudged and not acted:
                                nudged = True
                                convo.append({"role": "assistant", "content": reply})
                                convo.append({"role": "user", "content": (
                                    "(system) You did NOT emit a tool block, so nothing "
                                    "actually happened — you only described it. If I asked "
                                    "you to paint/write/create/run something, do it NOW for "
                                    "real by emitting the ```tool block in the exact JSON "
                                    "format. Don't narrate the result; produce it.")})
                                continue
                            break
                        # Record her turn, then run each tool and collect results.
                        acted = True
                        convo.append({"role": "assistant", "content": reply})
                        # Q6 lives on the 4070; free it before a GPU paint so
                        # ComfyUI has room (LM Studio JIT-reloads Q6 next turn).
                        if is_q6 and any(c["name"] == "paint" for c in calls):
                            emit({"type": "tool", "name": "(freeing VRAM for paint)",
                                  "args": {}})
                            lms_unload()
                        results = []
                        for call in calls:
                            emit({"type": "tool", "name": call["name"],
                                  "args": call["args"]})
                            res = local_tools.run_tool(call["name"], call["args"])
                            emit({"type": "tool_result", "name": call["name"],
                                  "ok": res.get("ok", False),
                                  "summary": res.get("summary", "")})
                            art = res.get("artifact")
                            if art and art.get("url"):
                                # type is image | audio | video
                                emit({"type": art.get("type", "image"),
                                      "url": art["url"]})
                            results.append(f"[result of {call['name']}]\n"
                                           + res.get("summary", ""))
                        convo.append({"role": "user",
                                      "content": "\n\n".join(results)})
                    emit({"type": "final"})
                except Exception as e:
                    emit({"type": "error", "content": str(e)})
            else:
                self.send_error(404, "Not Found")
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(str(e).encode('utf-8'))

if __name__ == '__main__':
    with http.server.ThreadingHTTPServer(('127.0.0.1', PORT), EchoWinHandler) as httpd:
        print(f"Echo-Win on http://127.0.0.1:{PORT}")
        httpd.serve_forever()
