#!/usr/bin/env python
"""verify_edit.py - syntax-check a just-edited file.

Two modes:
  * Claude Code PostToolUse hook: reads the hook JSON on stdin, pulls
    tool_input.file_path, and syntax-checks it. On failure it writes the
    error to stderr and exits 2 (so Claude Code surfaces it); otherwise it
    exits 0 silently.
  * CLI / aider --lint-cmd: `py verify_edit.py path/to/file`.

Only .py (py_compile) and .js (node --check) are checked; every other file
type passes silently. Restored 2026-06-30 (the original went missing); spec
matches the MECHANICAL VERIFICATION LAYER note in memory.
"""
import sys, os, json, subprocess


def find_path():
    # An explicit CLI argument wins (CLI / lint-cmd use).
    if len(sys.argv) > 1:
        return sys.argv[1]
    # Otherwise read the PostToolUse hook payload from stdin.
    try:
        raw = sys.stdin.read()
    except Exception:
        return None
    if not raw.strip():
        return None
    try:
        obj = json.loads(raw)
    except Exception:
        return None
    ti = obj.get("tool_input") or {}
    return ti.get("file_path") or ti.get("path")


def check(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".py":
        return subprocess.run([sys.executable, "-m", "py_compile", path],
                              capture_output=True, text=True)
    if ext == ".js":
        return subprocess.run(["node", "--check", path],
                              capture_output=True, text=True)
    return None  # not a checked type


def main():
    path = find_path()
    if not path or not os.path.isfile(path):
        sys.exit(0)
    result = check(path)
    if result is None:
        sys.exit(0)
    if result.returncode != 0:
        msg = (result.stderr or result.stdout or "syntax check failed").strip()
        sys.stderr.write(msg + "\n")
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
