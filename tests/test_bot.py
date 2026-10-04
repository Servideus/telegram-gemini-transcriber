import httpx
import json
import asyncio
from pathlib import Path
from types import SimpleNamespace as NS
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
import wave

from google.genai import types
from telegram.error import BadRequest, RetryAfter

import my_telegram_bot as bot


class ApiError(Exception):
    def __init__(self, code):
        self.code = code


def message(chat=123, kind="voice", size=100):
    media = NS(file_name="note.mp3", mime_type="audio/mpeg", file_size=size,
               get_file=AsyncMock())
    return NS(chat=NS(id=chat, type="private"), voice=media if kind == "voice" else None,
              audio=media if kind == "audio" else None,
              document=media if kind == "document" else None, reply_text=AsyncMock())


class Helpers(unittest.TestCase):
    def test_all_audio_inputs_and_non_audio_rejection(self):
        for kind in ("voice", "audio", "document"):
            self.assertIsNotNone(bot.audio_attachment(message(kind=kind))[0])
        doc = message(kind="document")
        doc.document.file_name = "notes.txt"
        doc.document.mime_type = "text/plain"
        self.assertIsNone(bot.audio_attachment(doc)[0])

    def test_document_filename_cannot_escape_temp_directory(self):
        doc = message(kind="document")
        doc.document.file_name = "../../private.wav"
        self.assertEqual(bot.audio_attachment(doc)[1], ".wav")

    def test_long_unicode_transcript_roundtrip(self):
        text = "Привет 😀 " * 1200
        blocks = bot.split_text(text)
        self.assertEqual("".join(blocks), text.strip())
        self.assertTrue(all(len(block.encode("utf-16-le")) // 2 <= 4000 for block in blocks))

    def test_newline_split_never_emits_empty_block(self):
        blocks = bot.split_text("x\n" + "я" * 9000)
        self.assertTrue(all(blocks))
        self.assertTrue(all(len(block) <= 4000 for block in blocks))

    def test_config_check_does_not_search_parent_env(self):
        with patch.dict(bot.os.environ, {"TELEGRAM_BOT_TOKEN": "123:fake", "GEMINI_API_KEY": "fake"}, clear=True), patch.object(bot, "load_dotenv") as load:
            config = bot.Config.load()
        load.assert_called_once_with(bot.ROOT / ".env")
        self.assertIsNone(config.chat_id)

    def test_invalid_local_configuration_rejected(self):
        with patch.dict(bot.os.environ, {"TELEGRAM_BOT_TOKEN": "123:fake", "GEMINI_API_KEY": "fake", "TELEGRAM_LOCAL_MODE": "1"}, clear=True), patch.object(bot, "load_dotenv"):
            with self.assertRaisesRegex(ValueError, "TELEGRAM_BASE_URL"):
                bot.Config.load()


class Pipeline(unittest.IsolatedAsyncioTestCase):
    def context(self, **settings):
        return NS(bot_data={"config": bot.Config("123:fake", "fake", chat_id=123, **settings),
                            "transcriber": NS(transcribe=AsyncMock(return_value="Распознанный текст."))})

    async def test_setup_reveals_only_own_chat_id(self):
        msg = message()
        context = self.context()
        context.bot_data["config"] = bot.Config("123:fake", "fake")
        await bot.start(NS(effective_message=msg), context)
        self.assertIn("TELEGRAM_CHAT_ID=123", msg.reply_text.call_args.args[0])
        await bot.handle_audio(NS(effective_message=msg), context)
        msg.voice.get_file.assert_not_awaited()

    async def test_other_chats_and_groups_never_reach_api(self):
        for msg in (message(chat=456), message()):
            if msg.chat.id == 123:
                msg.chat.type = "group"
            context = self.context()
            await bot.start(NS(effective_message=msg), context)
            await bot.handle_audio(NS(effective_message=msg), context)
            msg.reply_text.assert_not_awaited()
            context.bot_data["transcriber"].transcribe.assert_not_awaited()

    async def test_cloud_large_file_rejected_before_download(self):
        msg = message(size=21 * 1024 * 1024)
        await bot.handle_audio(NS(effective_message=msg), self.context())
        msg.voice.get_file.assert_not_awaited()
        self.assertIn("20 MB", msg.reply_text.call_args.args[0])

    async def test_voice_to_transcript_and_temp_cleanup(self):
        for kind in ("voice", "audio", "document"):
            msg = message(kind=kind)
            media, _ = bot.audio_attachment(msg)
            paths = []
            async def download(path, **kwargs):
                paths.append(path)
                path.write_bytes(b"test audio")
            media.get_file.return_value = NS(download_to_drive=AsyncMock(side_effect=download))
            status = NS(edit_text=AsyncMock())
            msg.reply_text.return_value = status
            context = self.context()
            await bot.handle_audio(NS(effective_message=msg), context)
            status.edit_text.assert_awaited_once_with("Распознанный текст.")
            self.assertFalse(paths[0].parent.exists())
            self.assertEqual(media.get_file.call_args.kwargs["read_timeout"], 120)

    async def test_failure_still_cleans_download(self):
        msg = message()
        paths = []
        async def download(path, **kwargs):
            paths.append(path)
            path.write_bytes(b"test audio")
        msg.voice.get_file.return_value = NS(download_to_drive=AsyncMock(side_effect=download))
        status = NS(edit_text=AsyncMock())
        msg.reply_text.return_value = status
        context = self.context()
        context.bot_data["transcriber"].transcribe.side_effect = ApiError(401)
        with patch.object(bot, "to_wav", new_callable=AsyncMock) as convert:
            await bot.handle_audio(NS(effective_message=msg), context)
        convert.assert_not_awaited()
        self.assertFalse(paths[0].parent.exists())
        self.assertIn("Не удалось", status.edit_text.call_args.args[0])

    async def test_format_failure_uses_wav(self):
        msg = message()
        async def download(path, **kwargs):
            path.write_bytes(b"bad format")
        msg.voice.get_file.return_value = NS(download_to_drive=AsyncMock(side_effect=download))
        msg.reply_text.return_value = NS(edit_text=AsyncMock())
        context = self.context()
        context.bot_data["transcriber"].transcribe.side_effect = [ApiError(400), "Текст после WAV."]
        async def convert(source, *args):
            wav = source.with_suffix(".wav")
            wav.write_bytes(b"converted")
            return wav
        with patch.object(bot, "to_wav", side_effect=convert):
            await bot.handle_audio(NS(effective_message=msg), context)
        self.assertEqual(context.bot_data["transcriber"].transcribe.await_count, 2)
        msg.reply_text.return_value.edit_text.assert_awaited_once_with("Текст после WAV.")

    async def test_flood_control_retries_only_unsent_part(self):
        msg = message()
        status = NS(edit_text=AsyncMock())
        msg.reply_text.side_effect = [RetryAfter(2), NS(), NS()]
        text = "а" * 8500
        with patch.object(bot.asyncio, "sleep", new_callable=AsyncMock) as sleep:
            await bot.deliver(msg, status, text)
        status.edit_text.assert_awaited_once()
        self.assertEqual(msg.reply_text.await_count, 3)
        self.assertEqual(msg.reply_text.call_args_list[0], msg.reply_text.call_args_list[1])
        sleep.assert_awaited_once_with(2.1)

    async def test_deleted_placeholder_gets_new_message(self):
        msg = message()
        status = NS(edit_text=AsyncMock(side_effect=BadRequest("message to edit not found")))
        await bot.deliver(msg, status, "Текст")
        msg.reply_text.assert_awaited_once_with("Текст")

    async def test_actual_ffmpeg_conversion(self):
        if not bot.shutil.which("ffmpeg"):
            self.skipTest("FFmpeg not installed")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.wav"
            with wave.open(str(source), "wb") as output:
                output.setparams((2, 2, 44100, 0, "NONE", "not compressed"))
                output.writeframes(b"\0" * 44100 * 4)
            converted = await bot.to_wav(source, "ffmpeg", 10)
            with wave.open(str(converted), "rb") as audio:
                self.assertEqual((audio.getnchannels(), audio.getsampwidth(), audio.getframerate()), (1, 2, 16000))


class Gemini(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.api = NS(files=NS(upload=AsyncMock(), get=AsyncMock(), delete=AsyncMock()),
                      models=NS(generate_content=AsyncMock()), aclose=AsyncMock())
        client = NS(aio=self.api, close=lambda: None)
        with patch.object(bot.genai, "Client", return_value=client):
            self.transcriber = bot.Transcriber(bot.Config("123:fake", "fake", models=("first", "second")))

    async def test_quota_and_server_failures_use_next_model(self):
        for code in (429, 503, 404, 401, 403):
            self.api.models.generate_content.reset_mock()
            self.api.models.generate_content.side_effect = [ApiError(code), NS(text="Текст")]
            with patch.object(bot.asyncio, "sleep", new_callable=AsyncMock):
                self.assertEqual(await self.transcriber.generate(NS()), "Текст")
            self.assertEqual(self.api.models.generate_content.call_args.kwargs["model"], "second")

    async def test_auth_failure_attempts_each_model_once(self):
        self.api.models.generate_content.side_effect = ApiError(401)
        with self.assertRaises(RuntimeError):
            await self.transcriber.generate(NS())
        self.assertEqual(self.api.models.generate_content.await_count, 2)

    async def test_empty_and_timeout_advance_once(self):
        for first in (NS(text=" "), TimeoutError()):
            self.api.models.generate_content.reset_mock()
            self.api.models.generate_content.side_effect = [first, NS(text="OK")]
            self.assertEqual(await self.transcriber.generate(NS()), "OK")
            self.assertEqual(self.api.models.generate_content.await_count, 2)

    async def test_transcribe_verbatim_request(self):
        self.transcriber.config = bot.Config("123:fake", "fake")
        real_client = httpx.AsyncClient
        def respond(request):
            body = json.loads(request.content)
            self.assertFalse(body["store"])
            self.assertEqual(body["generation_config"]["transcription_config"]["mode"], {"type": "verbatim"})
            self.assertEqual(body["input"][0]["uri"], "https://example.test/audio")
            return httpx.Response(200, json={"steps": [{"type": "model_output", "content": [{"type": "text", "text": "Ну, привет."}]}]})
        with patch.object(bot.httpx, "AsyncClient", side_effect=lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs)):
            self.assertEqual(await self.transcriber.generate(NS(uri="https://example.test/audio", mime_type="audio/ogg")), "Ну, привет.")
        self.api.models.generate_content.assert_not_awaited()

    async def test_processing_state_and_remote_audio_deletion(self):
        self.api.files.upload.return_value = types.File(name="files/test", state=types.FileState.PROCESSING)
        self.api.files.get.return_value = types.File(name="files/test", state=types.FileState.ACTIVE)
        self.api.models.generate_content.return_value = NS(text="Текст")
        with patch.object(bot.asyncio, "sleep", new_callable=AsyncMock):
            self.assertEqual(await self.transcriber.transcribe(Path("audio.wav")), "Текст")
        self.api.files.get.assert_awaited_once_with(name="files/test")
        self.api.files.delete.assert_awaited_once_with(name="files/test")

    async def test_remote_audio_deleted_even_on_generation_error(self):
        self.api.files.upload.return_value = types.File(name="files/test", state=types.FileState.ACTIVE)
        self.api.models.generate_content.side_effect = ApiError(401)
        with self.assertRaises(RuntimeError):
            await self.transcriber.transcribe(Path("audio.wav"))
        self.api.files.delete.assert_awaited_once_with(name="files/test")


if __name__ == "__main__":
    unittest.main()
