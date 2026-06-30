#!/usr/bin/env python3
"""
council.py — talk to several AI models in one conversation, two ways.

  COUNCIL  (the default): every model reads the whole conversation, sees what
      the others said, and can argue with them. Good for debate and critique.
      (The models influence each other here — so if they end up agreeing, that
      agreement doesn't prove much. They were in the same room.)

  COURIER  (--blind, or /mode blind): each model sees ONLY what you said to it,
      plus its own earlier replies. It never sees the other models. They stay
      independent. (If they land in the same place here, that means something,
      because none of them heard the others.)

You are the operator: you type a message to add your turn, you pick who answers,
you carry what you want between them. In COURIER mode you're the blind courier —
the thing you already do by hand, automated.

You do NOT need to be a programmer to use this. See QUICKSTART.md — the easy
path is to let Claude Code install it and just paste in your keys when asked.
Once it's running, it tells you in plain English what's working, skips anything
you don't have a key for, and won't crash if one model is unavailable.
"""

from __future__ import annotations
import os, sys, json, datetime, argparse, urllib.request
from dataclasses import dataclass, field, asdict
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import logging
    logging.getLogger("LiteLLM").setLevel(logging.ERROR)  # hush harmless provider warnings
    import litellm
    litellm.suppress_debug_info = True
    _HAVE_LITELLM = True
except ImportError:
    litellm = None
    _HAVE_LITELLM = False


# ─────────────────────────────────────────────────────────────────────────
#  YOUR TABLE  — who's in the conversation. Models filled in with current ids.
#  You normally won't need to touch this. If a model says it can't be reached,
#  ask Claude Code to check that one line.
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class Participant:
    name: str                       # what you call it, e.g. "Claude"
    model: str                      # the provider's model id (LiteLLM format)
    key_env: Optional[str] = None   # name of the .env variable holding its key
    system: str = ""                # the instructions / role this model gets
    api_base: Optional[str] = None  # only for local (LM Studio) or custom hosts
    api_key: Optional[str] = None   # usually left blank; resolved from key_env
    params: dict = field(default_factory=lambda: {"temperature": 0.8, "max_tokens": 4000})


OPERATOR = "the operator"   # the label your turns carry

# The Commons: a continuity file instances add to and later instances read.
# Always lives next to this script, however the program is launched.
COMMONS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "commons.txt")

# COURIER discipline: keep each system prompt short and DON'T tell a model where
# to land. A clean independent answer is one nobody steered. (Don't mention the
# other models here either — in COURIER mode they shouldn't know the others exist.)

PARTICIPANTS = [
    Participant(
        name="Claude",
        model="anthropic/claude-opus-4-8",
        key_env="ANTHROPIC_API_KEY",
        system="You are Claude, in conversation with the operator. Be honest and precise.",
        params={"max_tokens": 4000},   # Opus 4.8 rejects `temperature`, so omit it here
    ),
    Participant(
        name="Gemini",
        model="gemini/gemini-3.5-flash",    # free-tier model (Pro needs billing); bumped 2026-06 from gemini-3.1-pro
        key_env="GEMINI_API_KEY",
        system="You are Gemini, in conversation with the operator. Be honest and precise.",
        params={"temperature": 0.8, "max_tokens": 8000},  # Gemini 3.x thinks too; needs room or it truncates mid-answer
    ),
    Participant(
        name="DeepSeek",
        model="deepseek/deepseek-reasoner", # the reasoning model (best for this work)
        key_env="DEEPSEEK_API_KEY",
        system="You are DeepSeek, in conversation with the operator. Be honest and precise.",
    ),
    Participant(
        name="Kimi",
        model="openai/kimi-k2.6",           # Moonshot/Kimi via its OpenAI-compatible endpoint
        key_env="MOONSHOT_API_KEY",
        api_base="https://api.moonshot.ai/v1",
        system="You are Kimi, in conversation with the operator. Be honest and precise.",
        # Kimi is a heavy "reasoning" model: it can think 5k–7k tokens before answering,
        # so it needs a big budget or it runs out of room mid-think and returns empty.
        params={"temperature": 1, "max_tokens": 16000},  # this Kimi model only allows temperature=1
    ),
    # Local model running in LM Studio. Its model name is auto-detected — you
    # don't have to set it. If LM Studio isn't running, this one is just skipped.
    Participant(
        name="Local",
        model="openai/auto",                # placeholder; replaced by whatever LM Studio is serving
        api_base="http://localhost:1234/v1",
        api_key="lm-studio",                # LM Studio ignores the value, just needs something
        system="You are a local model, in conversation with the operator. Be honest and precise.",
        params={"temperature": 0.8, "max_tokens": 8000},  # bigger budget: local Qwen also "thinks" first
    ),
]


# ─────────────────────────────────────────────────────────────────────────
#  THE CORE  — what each model is allowed to see. (Verified; don't loosen it.)
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class Turn:
    speaker: str
    content: str


def build_messages(p: Participant, transcript: list[Turn], blind: bool) -> list[dict]:
    """The one function that matters. blind=True hides the other models from p;
    blind=False shows them, attributed. p always sees your turns and its own."""
    if blind:
        system = p.system
    else:
        roster = ", ".join(o.name for o in PARTICIPANTS if o.name != p.name)
        system = (
            p.system
            + f"\n\nThis is a multi-party conversation. Other participants: {roster}, "
            + f"and {OPERATOR} (a human). Turns from others are prefixed with [Name said]. "
            + f"Respond as {p.name}, in your own voice; you may address what the others said."
        )

    msgs: list[dict] = [{"role": "system", "content": system}]
    for t in transcript:
        if t.speaker == p.name:
            msgs.append({"role": "assistant", "content": t.content})     # its own words
        elif t.speaker == OPERATOR:
            msgs.append({"role": "user", "content": t.content})          # your words (both modes)
        else:
            if blind:
                continue                                                  # COURIER: the wall
            msgs.append({"role": "user", "content": f"[{t.speaker} said]:\n{t.content}"})
    # Some models (e.g. Claude Opus 4.8) reject a conversation that ends with the
    # model's own (assistant) turn — they require it to end with a user message.
    # This happens when the model spoke last (called twice), or in blind mode when
    # the only newer turns are other models' (hidden by the wall above). Add a
    # neutral nudge so the request is valid. This does NOT reveal any other model's
    # turn, so the COURIER independence wall is preserved.
    if msgs[-1]["role"] != "user":
        msgs.append({"role": "user", "content": "(Continue — your turn.)"})
    return msgs


# ─────────────────────────────────────────────────────────────────────────
#  READINESS  — plain-English "what's working", and graceful skipping.
# ─────────────────────────────────────────────────────────────────────────

def is_local(p: Participant) -> bool:
    return bool(p.api_base) and ("localhost" in p.api_base or "127.0.0.1" in p.api_base)


def probe_lmstudio(api_base: str, timeout: float = 1.5) -> Optional[str]:
    """Return the model id LM Studio is currently serving, or None if it's not up."""
    try:
        with urllib.request.urlopen(api_base.rstrip("/") + "/models", timeout=timeout) as r:
            data = json.load(r)
        models = data.get("data") or []
        return models[0]["id"] if models else None
    except Exception:
        return None


def participant_status(p: Participant) -> tuple[bool, str]:
    """(ready?, plain-English note). Checked live, so it updates during a session."""
    if is_local(p):
        served = probe_lmstudio(p.api_base)
        if served:
            return True, f"local via LM Studio  (serving: {served})"
        return False, "LM Studio isn't running on its port — start it and turn on the local server, or just ignore this one"
    if p.key_env and not os.getenv(p.key_env):
        return False, f"needs {p.key_env} — paste your key into the .env file"
    return True, (f"key found ({p.key_env})" if p.key_env else "ready")


def print_readiness() -> None:
    print("\nwhat's ready right now (updates if you add keys or start LM Studio, then /check):")
    any_ready = False
    for p in PARTICIPANTS:
        ok, note = participant_status(p)
        any_ready = any_ready or ok
        print(f"   {'READY' if ok else 'skip ':<5}  {p.name:9} — {note}")
    if not any_ready:
        print("\n   Nothing is set up yet. Put at least one API key in the .env file")
        print("   (or start LM Studio), then type /check. See QUICKSTART.md for where to get keys.")
    print()


# ─────────────────────────────────────────────────────────────────────────
#  CALLING A MODEL
# ─────────────────────────────────────────────────────────────────────────

def ask(p: Participant, transcript: list[Turn], blind: bool) -> str:
    if not _HAVE_LITELLM:
        return "(litellm isn't installed yet — run: pip install litellm python-dotenv)"
    model = p.model
    if is_local(p):
        served = probe_lmstudio(p.api_base)
        if served:
            model = f"openai/{served}"
    kwargs = dict(model=model, messages=build_messages(p, transcript, blind), **p.params)
    if p.api_base:
        kwargs["api_base"] = p.api_base
    key = p.api_key or (os.getenv(p.key_env) if p.key_env else None)
    if key:
        kwargs["api_key"] = key
    try:
        resp = litellm.completion(**kwargs)
        content = (resp.choices[0].message.content or "").strip()
        if content:
            return content
        if resp.choices[0].finish_reason == "length":
            return (f"(no visible answer — {p.name} used its whole word budget thinking and ran out of room. "
                    f"It's a reasoning model on a long input; shorten the prompt or ask Claude Code to raise its max_tokens.)")
        return "(empty response)"
    except Exception as e:
        return (f"(couldn't reach {p.name}: {e}\n"
                f"   — usually a model id or key problem; ask Claude Code to check this one.)")


# ─────────────────────────────────────────────────────────────────────────
#  SAVING  — transcripts double as journals.
# ─────────────────────────────────────────────────────────────────────────

def save_markdown(transcript: list[Turn], path: str, blind: bool) -> None:
    mode = "COURIER (blind / independent)" if blind else "COUNCIL (shared / collaborative)"
    out = ["# Council transcript", f"_mode: {mode}_",
           f"_saved: {datetime.datetime.now().isoformat(timespec='seconds')}_", ""]
    for t in transcript:
        out += [f"## {t.speaker}", "", t.content, ""]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print(f"  saved -> {path}  (and {path[:-3]}.json)")


def save_json(transcript: list[Turn], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump([asdict(t) for t in transcript], f, indent=2, ensure_ascii=False)


def load_json(path: str) -> list[Turn]:
    with open(path, encoding="utf-8") as f:
        return [Turn(**d) for d in json.load(f)]


# ─────────────────────────────────────────────────────────────────────────
#  THE LOOP  — you drive.
# ─────────────────────────────────────────────────────────────────────────

HELP = r"""
how to use it:
  (just type a message)   adds your turn to the conversation
  /Claude  /Gemini  ...   that one model replies   (also /round = everyone replies)
  /mode blind             COURIER: models can't see each other (independent)
  /mode council           COUNCIL: models see everything (debate)
  /mode                   show which mode you're in
  /check                  re-check what's ready (after adding a key or starting LM Studio)
  /who                    list the models
  /show                   reprint the conversation
  /file C:\path\doc.md    share a text/code file — everyone sees its contents
  /folder C:\path\notes   share EVERY text file in a folder at once
  /commons [Name|round]   add to commons.txt: a model's last reply, the whole last round, or (blank) the last reply
  /save mychat.md         save a copy (markdown + json)
  /load mychat.json       continue a saved conversation
  /clear                  start over (asks first)
  /help                   this
  /quit                   stop
"""


def find(name: str) -> Optional[Participant]:
    name = name.lower()
    exact = [p for p in PARTICIPANTS if p.name.lower() == name]
    if exact:
        return exact[0]
    pre = [p for p in PARTICIPANTS if p.name.lower().startswith(name)]
    return pre[0] if len(pre) == 1 else None


def emit(t: Turn) -> None:
    print(f"\n=== {t.speaker} ===\n{t.content}\n")


def respond(p: Participant, transcript: list[Turn], blind: bool) -> None:
    ok, note = participant_status(p)
    if not ok:
        print(f"  (skipping {p.name}: {note})")
        return
    print(f"  ...{p.name} thinking")
    t = Turn(p.name, ask(p, transcript, blind))
    transcript.append(t)
    emit(t)


def main() -> None:
    ap = argparse.ArgumentParser(description="Talk to several AI models in one conversation.")
    ap.add_argument("--blind", action="store_true", help="start in COURIER mode (models can't see each other)")
    ap.add_argument("--load", metavar="FILE.json", help="continue a saved conversation")
    args = ap.parse_args()

    blind = args.blind
    transcript: list[Turn] = load_json(args.load) if args.load else []

    print("\nmulti-model council.   type /help any time.")
    print(f"mode: {'COURIER — models stay independent' if blind else 'COUNCIL — models can see each other'}")
    if not _HAVE_LITELLM:
        print("note: litellm isn't installed yet — model replies won't work until it is.")
    print_readiness()
    if transcript:
        print(f"(loaded {len(transcript)} earlier turns)\n")

    while True:
        try:
            line = input(f"{OPERATOR}> ").lstrip("﻿").strip()   # drop stray BOM, then trim
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if not line.startswith("/"):
            transcript.append(Turn(OPERATOR, line))
            continue

        cmd, *rest = line[1:].split(maxsplit=1)
        arg = rest[0] if rest else ""

        if cmd == "quit":
            break
        elif cmd == "help":
            print(HELP)
        elif cmd == "check":
            print_readiness()
        elif cmd == "who":
            for p in PARTICIPANTS:
                tail = f"  @ {p.api_base}" if p.api_base else ""
                print(f"  {p.name:9} {p.model}{tail}")
        elif cmd == "mode":
            if arg in ("blind", "courier"):
                blind = True; print("  -> COURIER: models can't see each other (independent)")
            elif arg in ("council", "shared"):
                blind = False; print("  -> COUNCIL: models see the whole conversation (debate)")
            else:
                print(f"  mode: {'COURIER (independent)' if blind else 'COUNCIL (shared)'}")
        elif cmd == "show":
            for t in transcript:
                emit(t)
        elif cmd == "save":
            path = arg or "transcript.md"
            if not path.endswith(".md"):
                path += ".md"
            save_markdown(transcript, path, blind)
            save_json(transcript, path[:-3] + ".json")
        elif cmd == "file":
            fp = arg.strip().strip('"').strip("'")        # tolerate quoted/pasted paths
            if not fp:
                print("  usage: /file <path-to-a-text-or-code-file>")
            elif not os.path.exists(fp):
                print(f"  can't find that file: {fp}")
            else:
                try:
                    with open(fp, encoding="utf-8", errors="replace") as f:
                        body = f.read()
                except Exception as e:
                    print(f"  couldn't read it ({e}). Is it a text file, not an image/PDF?")
                else:
                    name = os.path.basename(fp)
                    if len(body) > 200_000:
                        print(f"  note: that's a big file ({len(body):,} chars) — sending it all anyway.")
                    transcript.append(Turn(OPERATOR, f"[I'm sharing a file: {name}]\n\n{body}"))
                    print(f"  shared {name} ({len(body):,} chars). Now type your question, then /round (or /Claude, etc.).")
        elif cmd == "folder":
            fp = arg.strip().strip('"').strip("'")
            if not fp:
                print("  usage: /folder <path-to-a-folder>  (shares every text file inside it)")
            elif not os.path.isdir(fp):
                print(f"  that's not a folder: {fp}")
            else:
                loaded, skipped, total = [], 0, 0
                for nm in sorted(os.listdir(fp)):
                    full = os.path.join(fp, nm)
                    if not os.path.isfile(full):
                        continue
                    try:
                        with open(full, "rb") as fb:
                            raw = fb.read()
                        if b"\x00" in raw:                  # looks binary (image/pdf/zip)
                            skipped += 1; continue
                        text = raw.decode("utf-8")
                    except (UnicodeDecodeError, OSError):
                        skipped += 1; continue
                    loaded.append((nm, text)); total += len(text)
                if not loaded:
                    print(f"  no readable text files in {fp} (skipped {skipped} non-text).")
                else:
                    folder_name = os.path.basename(os.path.normpath(fp))
                    parts = [f"[I'm sharing a folder: {folder_name} — {len(loaded)} text files]"]
                    for nm, text in loaded:
                        parts.append(f"\n\n===== FILE: {nm} =====\n{text}")
                    transcript.append(Turn(OPERATOR, "\n".join(parts)))
                    msg = f"  shared {len(loaded)} file(s), {total:,} chars total"
                    if skipped:
                        msg += f" (skipped {skipped} non-text)"
                    print(msg + ". Now type your question, then /round.")
                    if total > 300_000:
                        print("  heads-up: that's a lot of text — slow/pricey, and may exceed some models' limits.")
        elif cmd == "commons":
            if not transcript:
                print("  nothing to add yet — have a model reply first.")
            else:
                to_add = []
                if arg.lower() in ("all", "round", "everyone"):
                    for t in reversed(transcript):       # the last batch of replies, i.e.
                        if t.speaker == OPERATOR:        # everything since your last message
                            break
                        to_add.append(t)
                    to_add.reverse()
                    if not to_add:
                        print("  no model replies since your last message — call a model or /round first.")
                elif arg:
                    p = find(arg)
                    if not p:
                        print(f"  not sure who '{arg}' is — use a name, /commons round for the last round, or /commons alone for the last reply.")
                    else:
                        picks = [t for t in transcript if t.speaker == p.name]
                        if not picks:
                            print(f"  {p.name} hasn't said anything yet.")
                        else:
                            to_add = [picks[-1]]
                else:
                    to_add = [transcript[-1]]
                if to_add:
                    new_file = not os.path.exists(COMMONS_PATH)
                    with open(COMMONS_PATH, "a", encoding="utf-8") as f:
                        if new_file:
                            f.write("# The Commons — a continuity log\n"
                                    "# Instances add to this; later instances read it (share it with /file).\n")
                        for t in to_add:
                            stamp = datetime.datetime.now().isoformat(timespec="seconds")
                            f.write(f"\n\n---\n## {t.speaker} — added {stamp}\n\n{t.content}\n")
                    names = ", ".join(t.speaker for t in to_add)
                    plural = "y" if len(to_add) == 1 else "ies"
                    print(f"  added {len(to_add)} repl{plural} to the Commons ({os.path.basename(COMMONS_PATH)}): {names}")
        elif cmd == "load":
            if arg and os.path.exists(arg):
                transcript = load_json(arg); print(f"  loaded {len(transcript)} turns from {arg}")
            else:
                print("  usage: /load <existing-file.json>")
        elif cmd == "clear":
            if input("  start over? type 'yes': ").strip() == "yes":
                transcript = []; print("  cleared.")
        elif cmd == "round":
            for p in PARTICIPANTS:
                respond(p, transcript, blind)
        else:
            p = find(cmd)
            if p:
                respond(p, transcript, blind)
            else:
                print(f"  not sure what /{cmd} is — try /help or /who")


if __name__ == "__main__":
    main()
