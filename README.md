# Xiaomi MiMo Web CLI

AI-agent-native CLI for [Xiaomi MiMo Studio](https://aistudio.xiaomimimo.com) via browser-cookie auth. Zero-config, no API key needed.

**~10-15s latency** — uses Playwright browser automation (no Cloudflare on MiMo).

## Quick Start

```bash
pip install -r requirements.txt
playwright install chromium

# Login to MiMo in your browser first, then:
python mimo.py "Explain quantum computing"

# Agent-optimized (token-efficient JSON pointer)
python mimo.py -o /tmp/out.md "Your prompt"
# → {"f":"/tmp/out.md","s":450,"b":2}
```

## Features

- Text prompts via browser-cookie auth (no API key)
- Multi-turn conversations (`-c chat.json`)
- Model switching (`-m mimo-v2.5`, `-m mimo-v2-flash`)
- Thinking toggle (`--no-thinking`)
- List available models (`--list-models` — no auth needed)
- Token-efficient JSON pointer output
- Cross-platform (Linux, macOS, Windows, WSL)

## Usage

```bash
# Basic
python mimo.py "Hello"

# List available models (no login needed)
python mimo.py --list-models

# Multi-turn conversation
python mimo.py -c chat.json "My name is Peter"
python mimo.py -c chat.json "What's my name?"

# Model selection
python mimo.py -m mimo-v2.5 "Multimodal task"

# Disable thinking for faster response
python mimo.py --no-thinking "Quick question"

# Output to file (stdout gets JSON pointer)
python mimo.py -o /tmp/result.md "Write a poem"

# JSON output
python mimo.py --json "Hello"

# Login flow (if auth expires)
python mimo.py --login

# Pipe from stdin
echo "What is 2+2?" | python mimo.py
```

## Auth

Extracts cookies from your browser automatically:
- **macOS/Linux**: Firefox → Chrome → Edge
- **Windows/WSL**: Firefox (SQLite) → Chrome → Edge

Or set `MIMO_COOKIE` env var with your cookie header.

## Models

| Model ID | Display Name | Description |
|----------|-------------|-------------|
| `mimo-v2.5-pro` | MiMo-V2.5-Pro | Open-Source Flagship Performance Model (default) |
| `mimo-v2.5` | MiMo-V2.5 | Large-Scale Multimodal Understanding |
| `mimo-v2-flash` | MiMo-V2-Flash | Fast model |

Plus TTS models for voice synthesis.

## How It Works

MiMo uses a React SPA with Xiaomi Account OAuth. This CLI launches headless Chromium, injects your browser cookies, types into the chat textarea, and polls for response completion via body-length growth detection.

**Note**: A direct HTTP API at `/open-apis/chat` exists but requires server-side session validation beyond cookies. Browser automation is the reliable path.

## Dependencies

- `playwright` — browser automation
- `browser-cookie3` — cookie extraction from browser

## License

MIT
