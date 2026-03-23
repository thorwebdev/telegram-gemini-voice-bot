"""
Telegram Gemini Voice Bot
=========================
A webhook-based Telegram bot using Gemini 3.1 Flash Lite for text/voice
reasoning and Gemini TTS for voice responses. Designed for Cloud Run.
"""

import io
import logging
import os
import struct
import wave
from dataclasses import dataclass, field

from google import genai
from google.genai import types
from pydub import AudioSegment
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GOOGLE_API_KEY = os.environ["GOOGLE_API_KEY"]
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
PORT = int(os.environ.get("PORT", "8080"))

REASONING_MODEL = "gemini-3.1-flash-lite-preview"
TTS_MODEL = "gemini-2.5-flash-preview-tts"
TTS_VOICE = "Kore"  # Natural-sounding voice

SYSTEM_INSTRUCTION = (
    "You are a helpful, concise AI assistant on Telegram. "
    "Keep responses short and informative. Use plain text formatting "
    "(no markdown) since Telegram voice messages are audio-only."
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Gemini client
# ---------------------------------------------------------------------------

gemini_client = genai.Client(api_key=GOOGLE_API_KEY)

# ---------------------------------------------------------------------------
# Per-chat voice preference (in-memory, resets on restart)
# ---------------------------------------------------------------------------

voice_prefs: dict[int, bool] = {}
DEFAULT_VOICE_ENABLED = os.environ.get("VOICE_ENABLED", "true").lower() == "true"


def is_voice_enabled(chat_id: int) -> bool:
    return voice_prefs.get(chat_id, DEFAULT_VOICE_ENABLED)


# ---------------------------------------------------------------------------
# Gemini helpers
# ---------------------------------------------------------------------------


async def gemini_reason(text: str | None = None, audio_bytes: bytes | None = None) -> str:
    """Send text or audio to Gemini 3.1 Flash Lite and return the text response."""
    contents: list = []

    if audio_bytes is not None:
        contents.append(
            types.Part.from_bytes(data=audio_bytes, mime_type="audio/ogg")
        )
        # Add a prompt so the model knows what to do with the audio
        contents.append("Listen to this voice message and respond helpfully.")

    if text is not None:
        contents.append(text)

    response = gemini_client.models.generate_content(
        model=REASONING_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
        ),
    )
    return response.text or "(No response generated)"


async def gemini_tts(text: str) -> bytes:
    """Convert text to OGG/Opus audio bytes via Gemini TTS."""
    response = gemini_client.models.generate_content(
        model=TTS_MODEL,
        contents=text,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=TTS_VOICE,
                    )
                )
            ),
        ),
    )

    # Extract audio data from response
    audio_data = response.candidates[0].content.parts[0].inline_data
    pcm_audio = audio_data.data
    sample_rate = audio_data.mime_type.split("rate=")[-1] if "rate=" in audio_data.mime_type else "24000"
    sample_rate = int(sample_rate)

    # Convert raw PCM → WAV → OGG/Opus for Telegram
    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)  # 16-bit
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_audio)

    wav_buffer.seek(0)
    audio_segment = AudioSegment.from_wav(wav_buffer)

    ogg_buffer = io.BytesIO()
    audio_segment.export(ogg_buffer, format="ogg", codec="libopus")
    ogg_buffer.seek(0)
    return ogg_buffer.read()


# ---------------------------------------------------------------------------
# Telegram handlers
# ---------------------------------------------------------------------------


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    await update.message.reply_text(
        "👋 Hi! I'm your Gemini AI assistant.\n\n"
        "• Send me a **text message** and I'll respond with AI-powered answers.\n"
        "• Send me a **voice note** and I'll understand and reply.\n"
        "• Use /voice on or /voice off to toggle voice responses.\n\n"
        "Powered by Gemini 3.1 Flash Lite ⚡",
        parse_mode="Markdown",
    )


async def voice_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /voice on|off command."""
    chat_id = update.effective_chat.id
    args = context.args

    if not args or args[0].lower() not in ("on", "off"):
        status = "on" if is_voice_enabled(chat_id) else "off"
        await update.message.reply_text(
            f"🔊 Voice responses are currently **{status}**.\n"
            "Use `/voice on` or `/voice off` to change.",
            parse_mode="Markdown",
        )
        return

    enabled = args[0].lower() == "on"
    voice_prefs[chat_id] = enabled
    emoji = "🔊" if enabled else "🔇"
    await update.message.reply_text(
        f"{emoji} Voice responses turned **{'on' if enabled else 'off'}**.",
        parse_mode="Markdown",
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming text messages."""
    chat_id = update.effective_chat.id
    user_text = update.message.text

    logger.info("Text message from chat %s: %s", chat_id, user_text[:100])

    # Send typing indicator
    await update.message.chat.send_action("typing")

    # Get Gemini response
    response_text = await gemini_reason(text=user_text)

    # Send text response
    await update.message.reply_text(response_text)

    # Optionally send voice response
    if is_voice_enabled(chat_id):
        try:
            await update.message.chat.send_action("record_voice")
            ogg_audio = await gemini_tts(response_text)
            await update.message.reply_voice(voice=ogg_audio)
        except Exception as e:
            logger.error("TTS failed: %s", e)
            # Text was already sent, so we just log the error


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming voice messages."""
    chat_id = update.effective_chat.id

    logger.info("Voice message from chat %s", chat_id)

    # Send typing indicator
    await update.message.chat.send_action("typing")

    # Download voice file from Telegram
    voice = update.message.voice
    voice_file = await voice.get_file()
    audio_bytes = await voice_file.download_as_bytearray()

    # Get Gemini response from audio
    response_text = await gemini_reason(audio_bytes=bytes(audio_bytes))

    # Send text response
    await update.message.reply_text(response_text)

    # Optionally send voice response
    if is_voice_enabled(chat_id):
        try:
            await update.message.chat.send_action("record_voice")
            ogg_audio = await gemini_tts(response_text)
            await update.message.reply_voice(voice=ogg_audio)
        except Exception as e:
            logger.error("TTS failed: %s", e)


# ---------------------------------------------------------------------------
# Application setup
# ---------------------------------------------------------------------------


def main() -> None:
    """Start the bot."""
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Register handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("voice", voice_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    if WEBHOOK_URL:
        # Webhook mode (Cloud Run / production)
        logger.info("Starting webhook on port %s → %s", PORT, WEBHOOK_URL)
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path="webhook",
            webhook_url=f"{WEBHOOK_URL}/webhook",
        )
    else:
        # Polling mode (local development without public URL)
        logger.info("Starting polling mode (no WEBHOOK_URL set)")
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
