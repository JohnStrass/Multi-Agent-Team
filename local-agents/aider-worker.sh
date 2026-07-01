#!/usr/bin/env bash
# aider-worker.sh -- HARD LAW: all local-model edits to EXISTING files go through here.
#
# Delegates an in-place code edit to the local coder (qwen2.5-coder:14b via Ollama),
# auto-lints the result (py_compile for .py, node --check for .js) so the model
# self-corrects before Claude reviews, and git-commits the change.
#
# Usage:  bash aider-worker.sh "instruction" file1 [file2 ...]
#         bash aider-worker.sh spec.txt      file1 [file2 ...]   # 1st arg may be a file
#
# delegate.py stays for brand-new file generation, review, and Q&A.
# NOTE: --auto-lint only covers .py/.js; Kotlin edits are verified by Claude's gradle build.
set -e

AIDER="C:/Claude-LLM-Projects/local-agents/aider-venv/Scripts/aider.exe"
export OLLAMA_API_BASE="http://127.0.0.1:11434"
export PYTHONUTF8=1               # avoid cp1252 console crash on rich's block glyphs

if [ "$#" -lt 2 ]; then
    echo "usage: bash aider-worker.sh \"instruction|spec.txt\" file1 [file2 ...]" >&2
    exit 1
fi

INSTRUCTION="$1"; shift
if [ -f "$INSTRUCTION" ]; then MSG=(--message-file "$INSTRUCTION"); else MSG=(--message "$INSTRUCTION"); fi

"$AIDER" \
    --model ollama_chat/qwen2.5-coder:14b \
    --no-stream --no-pretty --yes-always --no-analytics --no-show-model-warnings \
    --auto-lint \
    --lint-cmd "python: py -m py_compile" \
    --lint-cmd "javascript: node --check" \
    "${MSG[@]}" "$@"
