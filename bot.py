"""
Telegram Gemini Voice Bot
=========================
A webhook-based Telegram bot using the Gemini Interactions API for text/voice
reasoning and Gemini TTS for voice responses. Designed for Cloud Run.
"""

import base64
import io
import logging
import os
import wave

from google import genai
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
TELEGRAM_SECRET_TOKEN = os.environ.get("TELEGRAM_SECRET_TOKEN")
PORT = int(os.environ.get("PORT", "8080"))

REASONING_MODEL = "gemini-3.1-flash-lite-preview"
TTS_MODEL = "gemini-2.5-flash-preview-tts"
TTS_VOICE = "Kore"  # Natural-sounding voice
TTS_PROMPT = (
    "# AUDIO PROFILE: Gemini Assistant\n"
    "## DIRECTOR'S NOTES\n"
    "Style: Warm, friendly, and conversational. Like a helpful mate "
    "chatting over coffee. Natural and approachable, never robotic.\n"
    "Accent: Friendly south London accent, as heard in Brixton or Peckham. "
    "Not posh, not Cockney — relaxed and modern south London.\n"
    "Pace: Natural conversational pace, not rushed.\n"
    "## TRANSCRIPT\n"
)

# ---------------------------------------------------------------------------
# Modes — each mode has a system instruction and an audio prompt
# ---------------------------------------------------------------------------

MODES = {
    "agent": {
        "label": "🤖 Agent",
        "description": "General AI assistant",
        "system_instruction": (
            "You are a helpful, concise AI assistant on Telegram. "
            "Keep responses short and informative. "
            "Always respond in the same language the user writes or speaks in."
        ),
        "audio_prompt": "Listen to this voice message and respond helpfully in the same language.",
    },
    "transcribe": {
        "label": "🎤 Transcribe",
        "description": "Transcribe voice to text",
        "system_instruction": (
            "You are a transcription assistant. Your only job is to "
            "accurately transcribe the audio you receive in the original language. "
            "Output only the transcription, nothing else. "
            "Do not add commentary or formatting."
        ),
        "audio_prompt": "Transcribe this audio exactly as spoken, preserving the original language.",
    },
    "translate": {
        "label": "🌐 Translate",
        "description": "Translate voice/text",
        "system_instruction": (
            "You are a translation assistant. Translate everything you receive "
            "into {target_language}. If the input is already in {target_language}, "
            "output it unchanged. Output only the translation, nothing else."
        ),
        "audio_prompt": "Translate the speech in this audio to {target_language}.",
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
last_interaction_ids: dict[int, str] = {}  # chat_id → interaction ID for multi-turn
translate_langs: dict[int, str] = {}  # chat_id → target language for translate mode
DEFAULT_VOICE_ENABLED = os.environ.get("VOICE_ENABLED", "true").lower() == "true"
DEFAULT_TRANSLATE_LANG = "English"


def is_voice_enabled(chat_id: int) -> bool:
    return voice_prefs.get(chat_id, DEFAULT_VOICE_ENABLED)


def get_mode(chat_id: int) -> str:
    return chat_modes.get(chat_id, DEFAULT_MODE)


def get_translate_language(chat_id: int) -> str:
    return translate_langs.get(chat_id, DEFAULT_TRANSLATE_LANG)


# ---------------------------------------------------------------------------
# Gemini helpers
# ---------------------------------------------------------------------------


async def gemini_interact(
    chat_id: int,
    text: str | None = None,
    audio_bytes: bytes | None = None,
    mode: str = DEFAULT_MODE,
) -> str:
    """Send text or audio via the Gemini Interactions API.

    Uses previous_interaction_id for server-side multi-turn context.
    """
    mode_config = MODES[mode]
    target_lang = get_translate_language(chat_id)

    # Resolve {target_language} templates for translate mode
    system_instruction = mode_config["system_instruction"].format(target_language=target_lang)
    audio_prompt = mode_config["audio_prompt"].format(target_language=target_lang)

    input_parts: list = []

    if audio_bytes is not None:
        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
        input_parts.append(
            {"type": "audio", "data": audio_b64, "mime_type": "audio/ogg"}
        )
        input_parts.append({"type": "text", "text": audio_prompt})

    if text is not None:
        input_parts.append({"type": "text", "text": text})

    # Use single text string if only one text part
    input_value = input_parts if len(input_parts) > 1 else input_parts[0]["text"] if input_parts and input_parts[0]["type"] == "text" else input_parts

    # Build kwargs
    kwargs: dict = {
        "model": REASONING_MODEL,
        "input": input_value,
        "system_instruction": system_instruction,
        "tools": [{"type": "google_search"}],
    }

    # Chain to previous interaction for multi-turn context
    prev_id = last_interaction_ids.get(chat_id)
    if prev_id:
        kwargs["previous_interaction_id"] = prev_id

    interaction = gemini_client.interactions.create(**kwargs)

    # Store interaction ID for next turn
    last_interaction_ids[chat_id] = interaction.id

    return interaction.outputs[-1].text or "(No response generated)"


async def gemini_tts(text: str) -> bytes:
    """Convert text to OGG/Opus audio bytes via Gemini TTS (Interactions API)."""
    interaction = gemini_client.interactions.create(
        model=TTS_MODEL,
        input=TTS_PROMPT + text,
        response_modalities=["AUDIO"],
        generation_config={
            "speech_config": {
                "language": "en-us",
                "voice": TTS_VOICE.lower(),
            }
        },
    )

    # Extract PCM audio from response (base64-encoded)
    pcm_audio = None
    for output in interaction.outputs:
        if output.type == "audio":
            pcm_audio = base64.b64decode(output.data)
            break

    if pcm_audio is None:
        raise RuntimeError("No audio output from TTS")

    # Convert raw PCM → WAV → OGG/Opus for Telegram
    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)  # 16-bit
        wav_file.setframerate(24000)
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
        "• /language — Set translation target language\n"
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
    last_interaction_ids.pop(chat_id, None)  # Reset conversation on mode change
    cfg = MODES[mode_key]

    desc = cfg["description"]
    if mode_key == "translate":
        desc += f" → {get_translate_language(chat_id)}"

    await query.edit_message_text(
        f"Switched to **{cfg['label']}** mode\n_{desc}_",
        parse_mode="Markdown",
    )


async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /language <lang> command to set translation target language."""
    chat_id = update.effective_chat.id
    args = context.args

    if not args:
        current = get_translate_language(chat_id)
        await update.message.reply_text(
            f"🌐 Translation target: **{current}**\n"
            "Use `/language Spanish` (or any language) to change.",
            parse_mode="Markdown",
        )
        return

    lang = " ".join(args).strip().title()
    translate_langs[chat_id] = lang
    await update.message.reply_text(
        f"🌐 Translation target set to **{lang}**.",
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

    # Get Gemini response via Interactions API
    response_text = await gemini_interact(chat_id, text=user_text, mode=mode)

    # Prefix with mode label
    full_response = f"{MODES[mode]['label']}: {response_text}"

    # Send text response
    await update.message.reply_text(full_response)

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

    # Get Gemini response from audio via Interactions API
    response_text = await gemini_interact(chat_id, audio_bytes=bytes(audio_bytes), mode=mode)

    # Prefix with mode label
    full_response = f"{MODES[mode]['label']}: {response_text}"

    # Send text response
    await update.message.reply_text(full_response)

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
    app.add_handler(CommandHandler("language", language_command))
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
            secret_token=TELEGRAM_SECRET_TOKEN,
        )
    elif os.environ.get("K_SERVICE"):
        # Cloud Run detected but no WEBHOOK_URL yet (first deploy).
        # Start a minimal HTTP server so the container health check passes.
        # The bot won't receive messages until WEBHOOK_URL is set.
        from http.server import HTTPServer, BaseHTTPRequestHandler

        service = os.environ["K_SERVICE"]
        logger.info(
            "Cloud Run detected (K_SERVICE=%s) but WEBHOOK_URL not set. "
            "Starting health-check server on port %s. "
            "Set WEBHOOK_URL to activate the bot.",
            service,
            PORT,
        )

        class HealthHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"OK - Set WEBHOOK_URL to activate the bot")

            def log_message(self, format, *args):
                pass

        HTTPServer(("0.0.0.0", PORT), HealthHandler).serve_forever()
    else:
        # Polling mode (local development without public URL)
        logger.info("Starting polling mode (no WEBHOOK_URL set)")
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
