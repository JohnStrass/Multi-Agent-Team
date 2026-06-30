# Echo

A small local web app that drives a local fine-tuned model (served via Ollama) and
cloud models, with a browser UI. Echo runs the model's tool calls locally on the
host machine — including image generation via a local ComfyUI and simple music
synthesis.

## Files
- `server.py` — HTTP server + model orchestration and tool-call loop
- `local_tools.py` — the local tools the model can call (logged, never self-installing)
- `music_synth.py` — simple music synthesis tool
- `index.html` — browser UI
- `Echo-Win.bat` — Windows launcher

## Run
```bash
python server.py        # serves on http://localhost:8808
```

## Configuration (environment variables)
All host-specific values are read from the environment, with sensible defaults:

| Variable | Default | Purpose |
|----------|---------|---------|
| `LOCAL_MODEL` | `gemma4-12b-local:latest` | Ollama model name |
| `LOCAL_OLLAMA` | `http://localhost:11434` | Ollama server URL |
| `LOCAL_Q6_MODEL` | `local-finetune-...q6_k.gguf` | LM Studio Q6 model id |
| `LMS_BIN` | `~/.lmstudio/bin/lms.exe` | path to the LM Studio CLI |

Cloud model API keys are read from the environment as well — never hardcode them.
