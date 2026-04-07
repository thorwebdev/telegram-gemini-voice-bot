# Build a Voice-Enabled Telegram Bot with Gemini AI 🎙️🤖

*Send it a voice message, get a spoken answer back — powered by Gemini's Interactions API and TTS.*

---

What if your Telegram bot could *listen*?

Not just read text — actually understand voice messages, reason about them, and talk back with a natural-sounding voice. That's what we're building today: a Telegram bot powered by Google's Gemini API that handles both text and voice, with multi-turn memory and text-to-speech replies.

Here's what it looks like in action:

1. You send a voice note in any language
2. Gemini understands the audio and generates a text response
3. The bot sends the text *and* speaks the reply back as a voice message

All in about 400 lines of Python. Let's build it.

## What We're Using

- **[python-telegram-bot](https://python-telegram-bot.org/)** — async Telegram Bot API wrapper
- **[Gemini Interactions API](https://ai.google.dev/gemini-api/docs/interactions)** — Google's unified API for text, audio, and multi-turn conversations
- **Gemini 3.1 Flash Lite** — fast, cost-efficient model for reasoning
- **Gemini 2.5 Flash TTS** — text-to-speech model with natural-sounding voices
- **pydub + ffmpeg** — audio format conversion (PCM → OGG/Opus for Telegram)

## Prerequisites

- Python 3.11+
- A [Telegram Bot Token](https://t.me/BotFather) (create a bot via @BotFather)
- A [Google AI API Key](https://aistudio.google.com/apikey)
- `ffmpeg` installed (`brew install ffmpeg` on macOS, `apt-get install ffmpeg` on Linux)

## Project Setup

Create a new directory and set up the basics:

```bash
mkdir telegram-gemini-voice-bot && cd telegram-gemini-voice-bot

# Create a virtual environment
python -m venv .venv && source .venv/bin/activate

# Install dependencies
pip install 'python-telegram-bot[webhooks]~=21.11' 'google-genai>=1.55.0' 'pydub~=0.25'
```

Create a `.env` file with your credentials:

```bash
# .env
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
GOOGLE_API_KEY=your-google-api-key
TELEGRAM_SECRET_TOKEN=generate-a-random-string-here
VOICE_ENABLED=true
```

## Step 1: The Skeleton

Create `bot.py` and start with imports and config:

```python
import base64
import io
import logging
import os
import wave

from google import genai
from pydub import AudioSegment
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# Config
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GOOGLE_API_KEY = os.environ["GOOGLE_API_KEY"]
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
TELEGRAM_SECRET_TOKEN = os.environ.get("TELEGRAM_SECRET_TOKEN")
PORT = int(os.environ.get("PORT", "8080"))

REASONING_MODEL = "gemini-3.1-flash-lite-preview"
TTS_MODEL = "gemini-2.5-flash-preview-tts"
TTS_VOICE = "Kore"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Initialize the Gemini client
gemini_client = genai.Client(api_key=GOOGLE_API_KEY)
```

We're using two Gemini models:

- **Flash Lite** for understanding text and audio — it's the fastest, cheapest model in the Gemini family, perfect for a chatbot.
- **Flash TTS** for generating voice replies — it produces natural speech with configurable voices.

## Step 2: Understanding Audio with the Interactions API

The Interactions API is Gemini's unified interface. Instead of juggling `generateContent` and manually tracking conversation history, you call `interactions.create()` and pass a `previous_interaction_id` for multi-turn — the server handles the rest.

Here's the core function that sends text or audio to Gemini:

```python
# Track conversation state (in-memory, resets on restart)
last_interaction_ids: dict[int, str] = {}  # chat_id → interaction ID

async def gemini_interact(
    chat_id: int,
    text: str | None = None,
    audio_bytes: bytes | None = None,
) -> str:
    """Send text or audio to Gemini, return the text response."""

    input_parts: list = []

    if audio_bytes is not None:
        # Encode audio as base64 for the API
        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
        input_parts.append(
            {"type": "audio", "data": audio_b64, "mime_type": "audio/ogg"}
        )
        input_parts.append(
            {"type": "text", "text": "Listen to this voice message and respond helpfully."}
        )

    if text is not None:
        input_parts.append({"type": "text", "text": text})

    # Simplify input if it's just a single text part
    if len(input_parts) == 1 and input_parts[0]["type"] == "text":
        input_value = input_parts[0]["text"]
    else:
        input_value = input_parts

    kwargs = {
        "model": REASONING_MODEL,
        "input": input_value,
        "system_instruction": (
            "You are a helpful, concise AI assistant on Telegram. "
            "Keep responses short and informative. "
            "Always respond in the same language the user writes or speaks in."
        ),
    }

    # Chain to previous interaction for multi-turn context
    prev_id = last_interaction_ids.get(chat_id)
    if prev_id:
        kwargs["previous_interaction_id"] = prev_id

    interaction = gemini_client.interactions.create(**kwargs)

    # Store this interaction's ID for the next turn
    last_interaction_ids[chat_id] = interaction.id

    return interaction.outputs[-1].text or "(No response generated)"
```

**What's happening here:**

1. **Audio input** — We base64-encode the voice message bytes and pass them as an `audio` part alongside a text prompt telling the model what to do.
2. **Multi-turn** — We store the `interaction.id` from each response and pass it as `previous_interaction_id` on the next call. The server keeps the full conversation history — we don't need to.
3. **Text input** — For plain text messages, we send a simple string instead of a multipart array.

## Step 3: Text-to-Speech with Gemini TTS

Gemini's TTS model returns raw PCM audio. Telegram voice messages require OGG/Opus format. So we need a conversion pipeline:

```
Text → Gemini TTS → raw PCM (24kHz, 16-bit, mono) → WAV → OGG/Opus → Telegram
```

Here's the implementation:

```python
async def gemini_tts(text: str) -> bytes:
    """Convert text to OGG/Opus audio bytes via Gemini TTS."""
    interaction = gemini_client.interactions.create(
        model=TTS_MODEL,
        input=text,
        response_modalities=["AUDIO"],
        generation_config={
            "speech_config": {
                "voice": TTS_VOICE.lower(),
            }
        },
    )

    # Extract PCM audio from response
    pcm_audio = None
    for output in interaction.outputs:
        if output.type == "audio":
            pcm_audio = base64.b64decode(output.data)
            break

    if pcm_audio is None:
        raise RuntimeError("No audio output from TTS")

    # Convert raw PCM → WAV (pydub needs a container format)
    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, "wb") as wav_file:
        wav_file.setnchannels(1)        # mono
        wav_file.setsampwidth(2)        # 16-bit
        wav_file.setframerate(24000)    # 24kHz
        wav_file.writeframes(pcm_audio)

    wav_buffer.seek(0)
    audio_segment = AudioSegment.from_wav(wav_buffer)

    # WAV → OGG/Opus (Telegram's required format for voice messages)
    ogg_buffer = io.BytesIO()
    audio_segment.export(ogg_buffer, format="ogg", codec="libopus")
    ogg_buffer.seek(0)
    return ogg_buffer.read()
```

The key detail: Gemini TTS returns **raw PCM** samples at 24kHz, 16-bit, mono. We wrap it in a WAV header using Python's `wave` module, then use `pydub` (which calls `ffmpeg` under the hood) to re-encode as OGG/Opus — the format Telegram expects for `reply_voice()`.

## Step 4: Telegram Handlers

Now wire it all together with Telegram's handler system. We need two handlers: one for text, one for voice.

### Handling Text Messages

```python
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming text messages."""
    chat_id = update.effective_chat.id
    user_text = update.message.text

    logger.info("Text message from chat %s: %s", chat_id, user_text[:100])

    # Show typing indicator
    await update.message.chat.send_action("typing")

    # Get Gemini response
    response_text = await gemini_interact(chat_id, text=user_text)

    # Always send text
    await update.message.reply_text(response_text)

    # Also send voice reply
    try:
        await update.message.chat.send_action("record_voice")
        ogg_audio = await gemini_tts(response_text)
        await update.message.reply_voice(voice=ogg_audio)
    except Exception as e:
        logger.error("TTS failed: %s", e)
```

### Handling Voice Messages

```python
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming voice messages."""
    chat_id = update.effective_chat.id

    logger.info("Voice message from chat %s", chat_id)

    await update.message.chat.send_action("typing")

    # Download voice file from Telegram (already in OGG/Opus format)
    voice = update.message.voice
    voice_file = await voice.get_file()
    audio_bytes = await voice_file.download_as_bytearray()

    # Send audio directly to Gemini — it understands OGG natively
    response_text = await gemini_interact(chat_id, audio_bytes=bytes(audio_bytes))

    # Send text response
    await update.message.reply_text(response_text)

    # Send voice response
    try:
        await update.message.chat.send_action("record_voice")
        ogg_audio = await gemini_tts(response_text)
        await update.message.reply_voice(voice=ogg_audio)
    except Exception as e:
        logger.error("TTS failed: %s", e)
```

The beautiful thing here: **Telegram voice messages are already OGG/Opus**, and Gemini understands that format directly. No transcoding needed on input — we just pass the raw bytes.

## Step 5: Launching the Bot

Finally, set up the application with both polling (local dev) and webhook (production) support:

```python
def main() -> None:
    """Start the bot."""
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Register handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    if WEBHOOK_URL:
        # Webhook mode (production / Cloud Run)
        logger.info("Starting webhook on port %s → %s", PORT, WEBHOOK_URL)
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path="webhook",
            webhook_url=f"{WEBHOOK_URL}/webhook",
            secret_token=TELEGRAM_SECRET_TOKEN,
        )
    else:
        # Polling mode (local dev — no public URL needed)
        logger.info("Starting polling mode (no WEBHOOK_URL set)")
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
```

**Polling vs. Webhook:**

- **Polling** — The bot asks Telegram "any new messages?" in a loop. Simple, works anywhere. Great for local development.
- **Webhook** — Telegram pushes messages to your URL. More efficient, required for serverless (Cloud Run). The `python-telegram-bot` library handles webhook registration automatically via `run_webhook()`.

## Running Locally

```bash
# Load environment variables
export $(cat .env | xargs)

# Start in polling mode (no WEBHOOK_URL = polling)
python bot.py
```

Open Telegram, find your bot, and send it a voice message. You should get back a text reply and a spoken response. 🎉

## Deploy to Cloud Run

Want this running 24/7 with scale-to-zero? Here's the Dockerfile:

```dockerfile
FROM python:3.12-slim

# Install ffmpeg for audio conversion (WAV → OGG/Opus)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .

ENV PORT=8080
EXPOSE 8080

CMD ["python", "bot.py"]
```

### 1. Initialize `gcloud` and Enable APIs

First, make sure your `gcloud` CLI is configured with the right project:

```bash
gcloud init --skip-diagnostics
```

Enable the required APIs — Secret Manager for storing credentials and Cloud Build for building your container:

```bash
gcloud services enable secretmanager.googleapis.com
gcloud services enable cloudbuild.googleapis.com
```

### 2. Store Secrets

Never put API keys in environment variables directly. Use Secret Manager:

```bash
echo -n "$(grep TELEGRAM_BOT_TOKEN .env | cut -d '=' -f2)" | \
  gcloud secrets create TELEGRAM_BOT_TOKEN --data-file=-
echo -n "$(grep GOOGLE_API_KEY .env | cut -d '=' -f2)" | \
  gcloud secrets create GOOGLE_API_KEY --data-file=-
echo -n "$(openssl rand -base64 32)" | \
  gcloud secrets create TELEGRAM_SECRET_TOKEN --data-file=-
```

> **Note:** The `echo -n` flag strips the trailing newline so it's not included in the stored secret. If you see a `%` at the end of the output when echoing — that's just zsh indicating no trailing newline, not part of your secret.

### 3. Grant IAM Permissions

Cloud Run source deploys use the **default Compute Engine service account** to build and run your container. This account needs three additional roles that aren't granted by default:

```bash
# Get your project number
PROJECT_NUMBER=$(gcloud projects describe $(gcloud config get-value project) \
  --format='value(projectNumber)')

# Allow the service account to build containers
gcloud projects add-iam-policy-binding $(gcloud config get-value project) \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/cloudbuild.builds.builder"

# Allow it to read uploaded source code from Cloud Storage
gcloud projects add-iam-policy-binding $(gcloud config get-value project) \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/storage.objectViewer"

# Allow it to access secrets at runtime
gcloud projects add-iam-policy-binding $(gcloud config get-value project) \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

**Why are these needed?** The default Compute Engine service account has the `roles/editor` role, but Editor doesn't include Cloud Build execution, fine-grained Cloud Storage read access, or Secret Manager access. This is a one-time setup per project.

### 4. Deploy

gcloud run deploy telegram-gemini-bot \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-secrets="TELEGRAM_BOT_TOKEN=TELEGRAM_BOT_TOKEN:latest,GOOGLE_API_KEY=GOOGLE_API_KEY:latest,TELEGRAM_SECRET_TOKEN=TELEGRAM_SECRET_TOKEN:latest" \
  --no-cpu-throttling
```

**Note on `--no-cpu-throttling`**: This tells Cloud Run to keep the CPU active even after the initial response is sent. Since the bot needs to process TTS and send a voice reply *after* acknowledging the message, this prevents the CPU from being throttled, which would otherwise cause the voice reply to be delayed or stall until the next message arrives.
    

Notice there's no `WEBHOOK_URL` here — and that's fine. The bot detects Cloud Run automatically via the `K_SERVICE` environment variable (which Cloud Run always sets) and starts the HTTP server on port 8080. It just won't register a webhook with Telegram yet, so it won't receive messages until Step 5.

### 5. Set the Real Webhook URL

Grab the actual service URL from the deploy output, then update the service:

```bash
gcloud run services update telegram-gemini-bot \
  --region us-central1 \
  --update-env-vars="WEBHOOK_URL=https://telegram-gemini-bot-xxxxx-uc.a.run.app"
```

Cloud Run gives you HTTPS, auto-scaling, and scale-to-zero — you only pay when someone actually messages the bot.

### Troubleshooting Deployment

| Error | Cause | Fix |
|---|---|---|
| `PERMISSION_DENIED: Build failed because the default service account is missing required IAM permissions` | Compute Engine service account lacks Cloud Build permissions | Grant `roles/cloudbuild.builds.builder` and `roles/storage.objectViewer` (see Step 3) |
| `Permission denied on secret` | Service account can't access Secret Manager | Grant `roles/secretmanager.secretAccessor` (see Step 3) |
| `API [secretmanager.googleapis.com] not enabled` | Secret Manager API hasn't been turned on | Run `gcloud services enable secretmanager.googleapis.com` |
| `API [cloudbuild.googleapis.com] not enabled` | Cloud Build API hasn't been turned on | Say `Y` when prompted, or run `gcloud services enable cloudbuild.googleapis.com` |
| `Voice replies are slow or delayed` | CPU is being throttled after the text response | Deploy with `--no-cpu-throttling` to keep CPU active for background tasks |
    

## The Key Architectural Ideas

### 1. Server-Side Conversation Memory

Traditional chatbot APIs make *you* manage the conversation history. You send the full history on every request, and your token costs grow with every turn.

The Interactions API flips this. You pass `previous_interaction_id` and the server keeps the context:

```python
# Turn 1
i1 = client.interactions.create(model="gemini-3.1-flash-lite-preview", input="Hi, I'm Alex")

# Turn 2 — server remembers "Alex"
i2 = client.interactions.create(
    model="gemini-3.1-flash-lite-preview",
    input="What's my name?",
    previous_interaction_id=i1.id  # ← that's it
)
```

In our bot, we key this by `chat_id`, so each Telegram chat gets its own conversation thread.

### 2. Multimodal Input Without Transcription

Gemini understands audio natively. No whisper, no transcription step, no intermediate text. We send the OGG bytes directly:

```python
input_parts = [
    {"type": "audio", "data": audio_b64, "mime_type": "audio/ogg"},
    {"type": "text", "text": "Listen and respond helpfully."},
]
```

This means the model hears *tone*, *emphasis*, and *language* — not just words. It can respond in the same language the user speaks, detect questions vs. statements, and pick up on nuance that'd be lost in transcription.

### 3. Two-Model Architecture

We use two different models for two different jobs:

| Job | Model | Why |
|---|---|---|
| Understanding + reasoning | `gemini-3.1-flash-lite-preview` | Cheapest, fastest — ideal for a chatbot |
| Text-to-speech | `gemini-2.5-flash-preview-tts` | Purpose-built for natural speech synthesis |

This is cheaper and better than using a single model for both. Flash Lite handles the thinking, TTS handles the speaking.

## Going Further

The [full source code](https://github.com/example/telegram-gemini-voice-bot) extends this with:

- **Mode switching** — Agent, Transcribe, and Translate modes with inline keyboards
- **Configurable voice toggle** — `/voice on|off` to control TTS responses
- **Language selection** — `/language Spanish` to set the translation target
- **Mode-specific system instructions** — each mode has tailored prompts

These are all just variations on the same `gemini_interact()` function with different `system_instruction` values. The core voice pipeline stays the same.

---

**TL;DR:** Gemini's Interactions API makes voice bots surprisingly simple. Audio goes in as base64, text comes out, TTS converts it back to speech. The server tracks conversation state so you don't have to. Add a Dockerfile and you've got a production-ready voice assistant on Cloud Run.

Happy hacking! 🚀
