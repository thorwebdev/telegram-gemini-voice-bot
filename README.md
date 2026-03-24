# Telegram Gemini Voice Bot 🤖🎙️

A Telegram bot powered by **Gemini 3.1 Flash Lite** for text & voice reasoning, with optional **Gemini TTS** voice responses. Deploy on **Cloud Run** with scale-to-zero.

## Features

- 💬 **Text messages** → AI-powered text responses
- 🎤 **Voice notes** → Audio understanding + text responses
- 🔊 **Voice replies** → Optional TTS responses via Gemini
- 🔀 **Modes** → Switch between Agent, Transcribe, and Translate
- ⚡ **Fast** → Flash Lite model for low-latency responses
- 🚀 **Cloud Run ready** → Webhook mode with auto-scaling

## Setup

### 1. Create a Telegram Bot

1. Open Telegram and message [@BotFather](https://t.me/BotFather)
2. Send `/newbot` and follow the prompts to pick a name and username
3. Copy the **bot token** (e.g., `123456:ABC-DEF...`) — this is your `TELEGRAM_BOT_TOKEN`

> **Note**: BotFather is only for creating the bot and getting the token. You do **not** set the webhook URL in BotFather — the bot code handles that automatically via the Telegram API.

### 2. Get a Google AI API Key

1. Go to [Google AI Studio](https://aistudio.google.com/apikey)
2. Create an API key — this is your `GOOGLE_API_KEY`

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

You also need `ffmpeg` installed for audio conversion:
- **macOS**: `brew install ffmpeg`
- **Linux**: `apt-get install ffmpeg`
- **Docker**: Already included in the Dockerfile

## Local Development

### Polling Mode (simplest)

No public URL needed — the bot polls Telegram for updates:

```bash
cp .env.example .env
# Edit .env: set TELEGRAM_BOT_TOKEN and GOOGLE_API_KEY
# Leave WEBHOOK_URL empty

python bot.py
```

### Webhook Mode (with ngrok)

```bash
ngrok http 8080
# Copy the https URL (e.g., https://abc123.ngrok.io)

# Set WEBHOOK_URL=https://abc123.ngrok.io in .env
python bot.py
```

## Deploy to Cloud Run

### Step 1: Store Secrets

```bash
# Create secrets in Google Cloud Secret Manager
echo -n "YOUR_TELEGRAM_TOKEN" | gcloud secrets create telegram-bot-token --data-file=-
echo -n "YOUR_GOOGLE_API_KEY" | gcloud secrets create google-api-key --data-file=-
```

### Step 2: Deploy (without webhook URL)

On the first deploy you don't know the Cloud Run URL yet, so deploy without it:

```bash
gcloud run deploy telegram-gemini-bot \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-secrets="TELEGRAM_BOT_TOKEN=telegram-bot-token:latest,GOOGLE_API_KEY=google-api-key:latest"
```

The deploy output will show the service URL, e.g.:
```
Service URL: https://telegram-gemini-bot-abc123-uc.a.run.app
```

### Step 3: Set the Webhook URL

Update the service with the URL from step 2:

```bash
gcloud run services update telegram-gemini-bot \
  --region us-central1 \
  --update-env-vars="WEBHOOK_URL=https://telegram-gemini-bot-abc123-uc.a.run.app"
```

This triggers a new revision. On startup, the bot automatically registers the webhook with Telegram — no manual setup needed.

### How Webhooks Work

- The bot calls the [Telegram `setWebhook` API](https://core.telegram.org/bots/api#setwebhook) automatically on startup
- Telegram then sends all updates as POST requests to `{WEBHOOK_URL}/webhook`
- If you redeploy with a new URL, the bot re-registers and overwrites the old webhook

**Manual webhook management** (usually not needed):
```bash
# Check current webhook
curl "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"

# Set webhook manually
curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://your-url.run.app/webhook"

# Remove webhook (switch to polling)
curl "https://api.telegram.org/bot<TOKEN>/deleteWebhook"
```

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | Bot token from @BotFather |
| `GOOGLE_API_KEY` | ✅ | Google AI API key |
| `WEBHOOK_URL` | ❌ | Public URL for webhooks (omit for polling mode) |
| `PORT` | ❌ | HTTP port (default: 8080) |
| `VOICE_ENABLED` | ❌ | Default voice response state (default: true) |

## Bot Commands

| Command | Description |
|---|---|
| `/start` | Welcome message & capabilities |
| `/mode` | Switch mode — inline keyboard with Agent, Transcribe, Translate |
| `/voice on` | Enable voice responses (Gemini TTS) |
| `/voice off` | Disable voice responses |

## Architecture

```
User ↔ Telegram API ↔ Cloud Run (bot.py)
                           ├─→ Gemini 3.1 Flash Lite (reasoning)
                           └─→ Gemini 2.5 Flash TTS (voice synthesis)
```
