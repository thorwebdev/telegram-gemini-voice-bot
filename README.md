# Telegram Gemini Voice Bot 🤖🎙️

A Telegram bot powered by **Gemini 3.1 Flash Lite** for text & voice reasoning, with optional **Gemini TTS** voice responses. Deploy on **Cloud Run** with scale-to-zero.

## Features

- 💬 **Text messages** → AI-powered text responses
- 🎤 **Voice notes** → Audio understanding + text responses
- 🔊 **Voice replies** → Optional TTS responses via Gemini
- ⚡ **Fast** → Flash Lite model for low-latency responses
- 🚀 **Cloud Run ready** → Webhook mode with auto-scaling

## Quick Start

### Prerequisites

- Python 3.12+
- [Telegram Bot Token](https://t.me/BotFather)
- [Google AI API Key](https://aistudio.google.com/apikey)
- `ffmpeg` installed (for audio conversion)

### Local Development

```bash
# Clone and install
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env with your tokens

# Run (polling mode - no webhook URL needed)
python bot.py
```

For webhook mode locally, use [ngrok](https://ngrok.com):

```bash
ngrok http 8080
# Set WEBHOOK_URL=https://your-id.ngrok.io in .env
python bot.py
```

### Deploy to Cloud Run

```bash
# Build and deploy
gcloud run deploy telegram-gemini-bot \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-secrets="TELEGRAM_BOT_TOKEN=telegram-bot-token:latest,GOOGLE_API_KEY=google-api-key:latest" \
  --set-env-vars="WEBHOOK_URL=https://telegram-gemini-bot-xxxxx.run.app"
```

> **Note**: Create secrets first via `gcloud secrets create`.

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | Bot token from @BotFather |
| `GOOGLE_API_KEY` | ✅ | Google AI API key |
| `WEBHOOK_URL` | ❌ | Public URL for webhooks (omit for polling mode) |
| `PORT` | ❌ | HTTP port (default: 8080) |
| `VOICE_ENABLED` | ❌ | Default voice response state (default: true) |

## Bot Commands

- `/start` — Welcome message & capabilities
- `/voice on` — Enable voice responses
- `/voice off` — Disable voice responses

## Architecture

```
User ↔ Telegram API ↔ Cloud Run (bot.py)
                           ├─→ Gemini 3.1 Flash Lite (reasoning)
                           └─→ Gemini TTS (voice synthesis)
```
