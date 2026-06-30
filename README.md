# Multi-Agent Team

A small command-line tool for running a turn-based conversation across several
large language models at once, plus a small local web app (Echo) and a couple of
LLM-format design docs.

## What's here
- **`council.py`** — CLI that runs a multi-model conversation in two modes:
  - **Council** — all models share the conversation and can build on each other.
  - **Courier** (`--blind`) — each model answers independently, never seeing the
    others. Useful for independent cross-model checks.
- **`council.bat`** — Windows launcher.
- **`requirements.txt`** — Python dependencies.
- **`.env.example`** — template for your API keys (copy to `.env`, fill in).
- **`echo-win/`** — a small local web app that drives a local model in the
  browser (see its own README).
- Docs: `llm-exchange-format-v0-SPEC.md`, `llm-format-preliminary-design.md`
  — a cross-model work-exchange format.

## Setup
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   |   macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then add your real keys
```

## Run
```bash
python council.py            # Council mode
python council.py --blind    # Courier (independent) mode
```

Transport is via LiteLLM (Anthropic, Gemini, DeepSeek, Moonshot/Kimi, and a
local LM Studio server). Any model without a key is simply skipped.

> **Note:** never commit your real `.env` — it holds live API keys. The included
> `.gitignore` already blocks it.

## License

Licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)** — see
[LICENSE](LICENSE). This is a strong copyleft license: anyone who modifies and
distributes this software, **or runs a modified version as a network service**,
must make their full source code available under the same license. In short — if
you build on this, your version has to stay open too.
