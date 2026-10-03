"""Personal Telegram audio transcription bot. No database or usage tracking."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import logging
import mimetypes
import os
from pathlib import Path
import shutil
import tempfile
from datetime import timedelta
from urllib.parse import urlparse

from dotenv import load_dotenv
from google import genai
from google.genai import types
from telegram import Update
from telegram.error import BadRequest, RetryAfter, TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

log = logging.getLogger("transcriber")
ROOT = Path(__file__).resolve().parent
AUDIO_EXTENSIONS = {".ogg", ".oga", ".opus", ".mp3", ".m4a", ".aac", ".wav", ".flac", ".wma", ".aiff", ".aif", ".amr"}
PROMPT = (
    "Распознай русскую речь в этом аудиофайле и преобразуй её в аккуратный письменный текст. "
    "Сохраняй факты и смысл, исправь орфографию и пунктуацию. Удали слова-паразиты, "
    "ложные старты и повторы фраз. Разбей текст на логические абзацы. "
    "Верни только конечный текст без пояснений."
)


@dataclass(frozen=True)
class Config:
    token: str
    api_key: str
    chat_id: int | None = None
    models: tuple[str, ...] = ("gemini-flash-latest", "gemini-flash-lite-latest")
    timeout: int = 420
    max_file_mb: int = 50
    ffmpeg: str = "ffmpeg"
    local_mode: bool = False
    base_url: str = ""
    base_file_url: str = ""

    @classmethod
    def load(cls):
        # Read this project's .env only; never search parent folders for keys.
        load_dotenv(ROOT / ".env")
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        key = os.getenv("GEMINI_API_KEY", "").strip()
        if not token or not key:
            raise ValueError("Заполните TELEGRAM_BOT_TOKEN и GEMINI_API_KEY в .env.")
        chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        models = tuple(m.strip() for m in os.getenv("GEMINI_MODELS", ",".join(cls.models)).split(",") if m.strip())
        timeout = int(os.getenv("REQUEST_TIMEOUT", "420"))
        max_file_mb = int(os.getenv("MAX_FILE_SIZE_MB", "50"))
        if not models or timeout <= 0 or max_file_mb <= 0:
            raise ValueError("Нужны непустой GEMINI_MODELS и положительные таймаут и размер файла.")
        base_url = os.getenv("TELEGRAM_BASE_URL", "").strip()
        base_file_url = os.getenv("TELEGRAM_BASE_FILE_URL", "").strip()
        local = os.getenv("TELEGRAM_LOCAL_MODE", "0").lower() in {"1", "true", "yes"}
        for url in (base_url, base_file_url):
            if url and (urlparse(url).scheme not in {"http", "https"} or not urlparse(url).hostname):
                raise ValueError("Telegram base URL должен быть HTTP(S)-адресом.")
        if local and not base_url:
            raise ValueError("Для TELEGRAM_LOCAL_MODE=1 задайте TELEGRAM_BASE_URL.")
        return cls(token, key, int(chat) if chat else None, models, timeout, max_file_mb,
                   os.getenv("FFMPEG_EXE", "ffmpeg"), local, base_url, base_file_url)


def error_code(error: Exception) -> int | None:
    value = getattr(error, "code", None)
    try:
        return int(value) if value is not None else None
    except (ValueError, TypeError):
        return None


def temporary_error(error: Exception) -> bool:
    return error_code(error) in {429, 500, 502, 503, 504} or isinstance(error, (TimeoutError, ConnectionError))


async def retry(call, attempts=3):
    for attempt in range(attempts):
        try:
            return await call()
        except Exception as error:
            if attempt == attempts - 1 or not temporary_error(error):
                raise
            log.warning("Temporary API failure: %s (%s)", type(error).__name__, error_code(error))
            await asyncio.sleep(0.6 * 2 ** attempt)


class Transcriber:
    def __init__(self, config: Config):
        self.config = config
        self.client = genai.Client(api_key=config.api_key,
                                   http_options=types.HttpOptions(timeout=config.timeout * 1000))
        self.api = self.client.aio

    async def close(self):
        await self.api.aclose()
        self.client.close()

    async def generate(self, uploaded):
        for index, model in enumerate(self.config.models):
            try:
                result = await retry(lambda: self.api.models.generate_content(
                    model=model, contents=[PROMPT, uploaded],
                    config=types.GenerateContentConfig(temperature=0)))
                text = (result.text or "").strip()
                if not text:
                    raise ValueError("Gemini вернул пустой текст.")
                log.info("Transcription completed with %s", model)
                return text
            except Exception as error:
                # Missing/retired models may be skipped; invalid credentials may not.
                fallback = temporary_error(error) or error_code(error) == 404
                if not fallback or index == len(self.config.models) - 1:
                    raise
                log.warning("Trying next model after %s (%s)", type(error).__name__, error_code(error))

    async def transcribe(self, path: Path) -> str:
        uploaded = None
        try:
            mime = mimetypes.guess_type(str(path))[0] or "audio/ogg"
            if path.suffix in {".oga", ".opus"}:
                mime = "audio/ogg"
            uploaded = await retry(lambda: self.api.files.upload(file=path, config={"mime_type": mime}))
            while uploaded.state == types.FileState.PROCESSING:
                await asyncio.sleep(1)
                uploaded = await retry(lambda: self.api.files.get(name=uploaded.name))
            if uploaded.state == types.FileState.FAILED:
                raise ValueError("Gemini не смог обработать аудиофайл.")
            return await self.generate(uploaded)
        finally:
            if uploaded is not None:
                try:
                    await asyncio.wait_for(self.api.files.delete(name=uploaded.name), timeout=10)
                except Exception as error:
                    log.warning("Remote audio cleanup failed: %s", type(error).__name__)


async def to_wav(source: Path, ffmpeg: str, timeout: int) -> Path:
    output = source.with_name("converted.wav")
    process = await asyncio.create_subprocess_exec(
        ffmpeg, "-nostdin", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(source), "-vn", "-ac", "1", "-ar", "16000",
        "-acodec", "pcm_s16le", str(output),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if process.returncode:
        # Do not expose paths or untrusted metadata in Telegram/log errors.
        raise ValueError("FFmpeg не смог преобразовать аудио.")
    return output


def audio_attachment(message):
    if message.voice:
        return message.voice, ".ogg"
    media = message.audio or message.document
    if media is None:
        return None, ""
    suffix = Path(media.file_name or "").suffix.lower()
    if message.audio or (media.mime_type or "").startswith("audio/") or suffix in AUDIO_EXTENSIONS:
        return media, suffix if suffix in AUDIO_EXTENSIONS else ".audio"
    return None, ""


def split_text(text: str, limit: int = 4000) -> list[str]:
    """Bound message length in UTF-16 units, including non-BMP characters."""
    blocks = []
    text = text.strip()
    while text:
        units = 0
        cut = 0
        for index, char in enumerate(text):
            units += 2 if ord(char) > 0xFFFF else 1
            if units > limit:
                break
            cut = index + 1
        if cut < len(text):
            boundary = text.rfind("\n", 0, cut)
            if boundary > 0:
                cut = boundary
        blocks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return blocks


async def telegram_call(call):
    # Retry only the unsent part after Telegram's requested flood-control delay.
    for attempt in range(3):
        try:
            return await call()
        except RetryAfter as error:
            if attempt == 2:
                raise
            delay = error.retry_after
            seconds = delay.total_seconds() if isinstance(delay, timedelta) else float(delay)
            await asyncio.sleep(seconds + 0.1)


async def deliver(message, status, text: str):
    blocks = split_text(text)
    try:
        await telegram_call(lambda: status.edit_text(blocks[0]))
    except BadRequest:
        # The placeholder may have been deleted. Do not replay other chunks.
        await telegram_call(lambda: message.reply_text(blocks[0]))
    for block in blocks[1:]:
        await telegram_call(lambda: message.reply_text(block))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    config = context.bot_data["config"]
    if message.chat.type != "private":
        return
    if config.chat_id is None:
        await message.reply_text(f"Ваш TELEGRAM_CHAT_ID={message.chat.id}. Укажите его в .env и перезапустите бота.")
    elif message.chat.id == config.chat_id:
        await message.reply_text("Отправьте голосовое сообщение или аудиофайл. Я верну текст с исправленной пунктуацией. Аудио передаётся в Gemini.")


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    config = context.bot_data["config"]
    # A single configured private chat, not a user registry or usage database.
    if config.chat_id is None or message.chat.type != "private" or message.chat.id != config.chat_id:
        return
    media, suffix = audio_attachment(message)
    if media is None:
        return
    file_size = int(media.file_size or 0)
    limit_mb = config.max_file_mb if config.local_mode else min(config.max_file_mb, 20)
    if file_size > limit_mb * 1024 * 1024:
        await message.reply_text(f"Файл слишком большой: лимит {limit_mb} MB. Для файлов больше 20 MB нужен локальный Telegram Bot API.")
        return
    status = await telegram_call(lambda: message.reply_text("Обрабатываю…"))
    try:
        with tempfile.TemporaryDirectory(prefix="telegram_transcribe_") as directory:
            source = Path(directory) / ("audio" + suffix)
            async with asyncio.timeout(config.timeout):
                tg_file = await media.get_file(read_timeout=120, connect_timeout=30)
                await tg_file.download_to_drive(source, read_timeout=120, connect_timeout=30)
                if source.stat().st_size > limit_mb * 1024 * 1024:
                    raise ValueError("Файл превышает заданный размер.")
                transcriber = context.bot_data["transcriber"]
                try:
                    text = await transcriber.transcribe(source)
                except Exception as error:
                    # Conversion helps format failures, not bad credentials or quotas.
                    if source.suffix == ".wav" or not (isinstance(error, ValueError) or error_code(error) == 400):
                        raise
                    wav = await to_wav(source, config.ffmpeg, config.timeout)
                    text = await transcriber.transcribe(wav)
            await deliver(message, status, text)
    except Exception as error:
        log.error("Audio processing failed: %s (%s)", type(error).__name__, error_code(error))
        text = "Превышено время ожидания. Попробуйте позже." if isinstance(error, TimeoutError) else "Не удалось обработать запись. Проверьте ключ, модель и доступность API."
        if isinstance(error, BadRequest) and "file is too big" in str(error).lower():
            text = "Telegram не скачал файл: для файлов больше 20 MB нужен локальный Bot API или запись частями."
        try:
            await telegram_call(lambda: status.edit_text(text))
        except TelegramError:
            log.warning("Could not deliver the error message.")


async def shutdown(application):
    await application.bot_data["transcriber"].close()


async def on_error(update, context):
    log.error("Telegram update failed: %s", type(context.error).__name__)


def build_application(config: Config) -> Application:
    builder = Application.builder().token(config.token).post_shutdown(shutdown)
    if config.base_url:
        builder.base_url(config.base_url)
    if config.base_file_url:
        builder.base_file_url(config.base_file_url)
    if config.local_mode:
        builder.local_mode(True)
    # ponytail: PTB's default sequential processing is enough for one private chat.
    app = builder.build()
    app.bot_data.update(config=config, transcriber=Transcriber(config))
    app.add_handler(CommandHandler("start", start, filters=filters.ChatType.PRIVATE))
    media_filter = filters.VOICE | filters.AUDIO | filters.Document.ALL
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & media_filter, handle_audio))
    app.add_error_handler(on_error)
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check configuration without network requests or polling.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        config = Config.load()
        if args.check:
            print("Configuration OK. No network requests or polling performed.")
            print("Private chat: configured" if config.chat_id is not None else "Setup mode: send /start and configure TELEGRAM_CHAT_ID.")
            print("FFmpeg: available" if shutil.which(config.ffmpeg) else "FFmpeg: unavailable (format conversion will fail).")
            return
        if config.chat_id is None:
            log.info("Setup mode; transcription disabled until TELEGRAM_CHAT_ID is configured.")
        # Explicit loop also works when run_polling is invoked on Python 3.13+.
        asyncio.set_event_loop(asyncio.new_event_loop())
        build_application(config).run_polling()
    except (ValueError, RuntimeError) as error:
        # Configuration errors contain variable names, never credential values.
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
