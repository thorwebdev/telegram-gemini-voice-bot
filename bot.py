"""
Telegram Gemini Voice Bot
=========================
A webhook-based Telegram bot using Gemini 3.1 Flash Lite for text/voice
reasoning and Gemini TTS for voice responses. Designed for Cloud Run.
"""

import io
import logging
import os
import wave

from google import genai
from google.genai import types
from pydub import AudioSegment
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
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

# ---------------------------------------------------------------------------
# Modes — each mode has a system instruction and an audio prompt
# ---------------------------------------------------------------------------

MODES = {
    "agent": {
        "label": "🤖 Agent",
        "description": "General AI assistant",
        "system_instruction": (
            "You are a helpful, concise AI assistant on Telegram. "
            "Keep responses short and informative. Use plain text formatting "
            "(no markdown) since Telegram voice messages are audio-only."
        ),
        "audio_prompt": "Listen to this voice message and respond helpfully.",
    },
    "transcribe": {
        "label": "🎤 Transcribe",
        "description": "Transcribe voice to text",
        "system_instruction": (
            "You are a transcription assistant. Your only job is to "
            "accurately transcribe the audio you receive. Output only the "
            "transcription, nothing else. Do not add commentary or formatting."
        ),
        "audio_prompt": "Transcribe this audio exactly as spoken.",
    },
    "translate": {
        "label": "🌐 Translate",
        "description": "Translate voice/text to English",
        "system_instruction": (
            "You are a translation assistant. Translate everything you receive "
            "into English. If the input is already in English, output it unchanged. "
            "Output only the translation, nothing else."
        ),
        "audio_prompt": "Translate the speech in this audio to English.",
    },
}

DEFAULT_MODE = "agent"

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
# Per-chat state (in-memory, resets on restart)
# ---------------------------------------------------------------------------

voice_prefs: dict[int, bool] = {}
chat_modes: dict[int, str] = {}
DEFAULT_VOICE_ENABLED = os.environ.get("VOICE_ENABLED", "true").lower() == "true"


def is_voice_enabled(chat_id: int) -> bool:
    return voice_prefs.get(chat_id, DEFAULT_VOICE_ENABLED)


def get_mode(chat_id: int) -> str:
    return chat_modes.get(chat_id, DEFAULT_MODE)


# ---------------------------------------------------------------------------
# Gemini helpers
# ---------------------------------------------------------------------------


async def gemini_reason(
    text: str | None = None,
    audio_bytes: bytes | None = None,
    mode: str = DEFAULT_MODE,
) -> str:
    """Send text or audio to Gemini 3.1 Flash Lite and return the text response."""
    mode_config = MODES[mode]
    contents: list = []

    if audio_bytes is not None:
        contents.append(
            types.Part.from_bytes(data=audio_bytes, mime_type="audio/ogg")
        )
        contents.append(mode_config["audio_prompt"])

    if text is not None:
        contents.append(text)

    response = gemini_client.models.generate_content(
        model=REASONING_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=mode_config["system_instruction"],
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
        "• Send me a **text message** or **voice note**\n"
        "• /mode — Switch between Agent, Transcribe, and Translate\n"
        "• /voice on|off — Toggle voice responses\n\n"
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


async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /mode command — show inline keyboard to pick a mode."""
    chat_id = update.effective_chat.id
    current = get_mode(chat_id)

    buttons = []
    for key, cfg in MODES.items():
        label = cfg["label"]
        if key == current:
            label += " ✓"
        buttons.append(InlineKeyboardButton(label, callback_data=f"mode:{key}"))

    await update.message.reply_text(
        f"Current mode: **{MODES[current]['label']}**\n"
        f"_{MODES[current]['description']}_\n\n"
        "Pick a mode:",
        reply_markup=InlineKeyboardMarkup([buttons]),
        parse_mode="Markdown",
    )


async def mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline keyboard button press for mode selection."""
    query = update.callback_query
    await query.answer()

    chat_id = update.effective_chat.id
    mode_key = query.data.split(":", 1)[1]

    if mode_key not in MODES:
        return

    chat_modes[chat_id] = mode_key
    cfg = MODES[mode_key]

    await query.edit_message_text(
        f"Switched to **{cfg['label']}** mode\n_{cfg['description']}_",
        parse_mode="Markdown",
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming text messages."""
    chat_id = update.effective_chat.id
    user_text = update.message.text

    logger.info("Text message from chat %s: %s", chat_id, user_text[:100])

    mode = get_mode(chat_id)

    # Send typing indicator
    await update.message.chat.send_action("typing")

    # Get Gemini response
    response_text = await gemini_reason(text=user_text, mode=mode)

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

    mode = get_mode(chat_id)

    # Send typing indicator
    await update.message.chat.send_action("typing")

    # Download voice file from Telegram
    voice = update.message.voice
    voice_file = await voice.get_file()
    audio_bytes = await voice_file.download_as_bytearray()

    # Get Gemini response from audio
    response_text = await gemini_reason(audio_bytes=bytes(audio_bytes), mode=mode)

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
    app.add_handler(CommandHandler("mode", mode_command))
    app.add_handler(CallbackQueryHandler(mode_callback, pattern=r"^mode:"))
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
