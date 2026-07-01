#!/usr/bin/env python
"""delegate.py - route a task to a model worker (local or cloud).

Lets Claude Code (the manager) hand work to other models instead of doing
everything in-session.

Usage:
    py delegate.py <worker> "task text"
    echo "task text" | py delegate.py <worker>          # task can come from stdin
    py delegate.py <worker> "instructions" < somefile   # arg + stdin are combined

Workers:
    qwen      qwen2.5-coder:14b  (Ollama @ localhost)      -> code generation
    gemma     gemma4-code-v2     (LM Studio @ :1234)       -> tested coding model
    deepseek  deepseek-reasoner  (api.deepseek.com)        -> cloud reasoner (review)
    kimi      kimi-k2.6          (api.moonshot.ai)         -> cloud reasoner (review)

Cloud keys are read from the project .env (never hardcoded). Env overrides:
    DELEGATE_SYSTEM   system prompt (default: concise coding assistant)
    DELEGATE_TEMP     sampling temperature (default: 0)
"""
import sys, os, json, time, urllib.request

ENV_PATH = r"C:\Claude-LLM-Projects\01 Github Local LLM project 01\Multi-LLM-Project\.env"

WORKERS = {
    "qwen":     {"backend": "ollama",   "host": "localhost",      "model": "qwen2.5-coder:14b"},
    "gemma":    {"backend": "lmstudio", "host": "localhost:1234", "model": "gemma4-code-v2"},
    "deepseek": {"backend": "cloud",    "host": "https://api.deepseek.com",  "model": "deepseek-reasoner",
                 "keyenv": "DEEPSEEK_API_KEY", "max_tokens": 8000},
    "kimi":     {"backend": "cloud",    "host": "https://api.moonshot.ai/v1", "model": "kimi-k2.6",
                 "keyenv": "MOONSHOT_API_KEY", "max_tokens": 16000, "temp": 1.0},
}

DEFAULT_SYSTEM = ("You are a focused coding assistant. Be correct and concise; "
                  "prefer the standard library. Return only what was asked for.")


def env_key(name):
    try:
        for line in open(ENV_PATH, encoding="utf-8"):
            line = line.strip()
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return os.environ.get(name, "")


def _post(url, payload, headers=None):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read())


def call_ollama(w, system, user, temp):
    d = _post(f"http://{w['host']}:11434/api/chat", {
        "model": w["model"], "stream": False, "options": {"temperature": temp},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    })
    return d["message"]["content"], d.get("eval_count", 0)


def call_lmstudio(w, system, user, temp):
    d = _post(f"http://{w['host']}/v1/chat/completions", {
        "model": w["model"], "temperature": temp, "max_tokens": 2048,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    })
    return d["choices"][0]["message"]["content"], d.get("usage", {}).get("completion_tokens", 0)


def call_cloud(w, system, user, temp):
    key = env_key(w["keyenv"])
    if not key:
        raise RuntimeError(f"no key for {w['keyenv']} in {ENV_PATH}")
    body = {
        "model": w["model"],
        "max_tokens": w.get("max_tokens", 4096),
        "temperature": w.get("temp", temp),
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    d = _post(w["host"].rstrip("/") + "/chat/completions", body, {"Authorization": f"Bearer {key}"})
    msg = d["choices"][0]["message"]
    return (msg.get("content") or "").strip() or "(empty response)", \
        d.get("usage", {}).get("completion_tokens", 0)


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in WORKERS:
        sys.exit(f"usage: py delegate.py <{'|'.join(WORKERS)}> \"task\"")
    name = sys.argv[1]
    task = " ".join(sys.argv[2:]).strip()
    if not sys.stdin.isatty():
        piped = sys.stdin.read().strip()
        if piped:
            task = (task + "\n\n" + piped).strip() if task else piped
    if not task:
        sys.exit("error: no task provided (give it as an argument or pipe it on stdin)")

    w = WORKERS[name]
    system = os.environ.get("DELEGATE_SYSTEM", DEFAULT_SYSTEM)
    temp = float(os.environ.get("DELEGATE_TEMP", "0"))
    fn = {"ollama": call_ollama, "lmstudio": call_lmstudio, "cloud": call_cloud}[w["backend"]]

    t0 = time.time()
    try:
        out, toks = fn(w, system, task, temp)
    except Exception as e:
        sys.exit(f"[{name}] call to {w['model']} failed: {e}")
    dt = time.time() - t0
    rate = toks / dt if dt else 0
    sys.stderr.write(f"[{name} -> {w['model']}: {dt:.1f}s, {toks} tok, {rate:.1f} tok/s]\n")
    print(out)


if __name__ == "__main__":
    main()
