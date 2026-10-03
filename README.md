[Инструкция на русском языке здесь](README.ru.md).

# Telegram Gemini Transcriber

A personal Telegram bot that turns Russian voice messages and audio files into edited text using Gemini. It accepts one configured private chat, splits long transcripts, retries temporary failures and removes temporary audio after processing. There is no database, user profile system, usage statistics, admin commands or daily quota.

Bring your own Telegram bot token and Gemini API key. Audio is sent to Google, and requests may consume paid quota. The prompt corrects punctuation and removes fillers; the output is an edited transcript.

## Setup

Verified on Windows 11 with Python 3.12.3. The bot does not use Windows APIs, but Linux has not been separately tested. FFmpeg is needed for format conversion when direct recognition fails.

```powershell
py -3.12 -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements-lock.txt
Copy-Item .env.example .env
```

Set your own [BotFather](https://t.me/BotFather) token and [Gemini API key](https://aistudio.google.com/apikey) in `.env`. Do not run two polling processes with the same bot token.

```powershell
./run.ps1 -Check
./run.ps1
```

`-Check` validates configuration without network requests; it does not validate credentials. `start_bot.bat` is the alternative Windows launcher. On Linux, create an environment, install dependencies and run `.venv/bin/python my_telegram_bot.py`.

## Configure your private chat

1. Leave `TELEGRAM_CHAT_ID` empty initially. Send `/start` to your bot in a private chat; it returns that chat's ID. Transcription is disabled at this stage.
2. Set the ID in `.env` and restart. Audio from other chats and groups is ignored.
3. Send a voice message, audio recording or audio document. The bot replaces its processing message with text and splits long responses.

Only `/start` is available. There is no user database.

## Configuration

| Variable | Purpose |
|---|---|
| `GEMINI_MODELS` | Ordered model list; default `gemini-flash-latest,gemini-flash-lite-latest` |
| `REQUEST_TIMEOUT` | Download/transcription timeout; default 420 seconds |
| `MAX_FILE_SIZE_MB` | File size ceiling; default 50 MB, with a 20 MB cloud Bot API download limit |
| `FFMPEG_EXE` | `ffmpeg` from PATH or an executable path |
| `TELEGRAM_LOCAL_MODE` | `1` for an already configured local Telegram Bot API |
| `TELEGRAM_BASE_URL` | Local server URL, for example `http://127.0.0.1:8081/bot` |
| `TELEGRAM_BASE_FILE_URL` | Optional local file URL, for example `http://127.0.0.1:8081/file/bot` |

Voice OGG, Telegram audio and audio documents share one handler. Recognized extensions: `.ogg`, `.oga`, `.opus`, `.mp3`, `.m4a`, `.aac`, `.wav`, `.flac`, `.wma`, `.aiff`, `.aif`, `.amr`. This does not guarantee support for every codec in Gemini or your FFmpeg installation.

Retries are bounded for 429 and temporary server errors. Unavailable models fall back to the next configured model; authentication errors do not. Thinking settings are left to the model because support differs.

## Files larger than 20 MB

The default cloud Bot API has a [20 MB download limit](https://core.telegram.org/bots/api#getfile). Larger files require a separately installed [local Telegram Bot API](https://github.com/tdlib/telegram-bot-api) running with `--local`. Telegram requires `logOut` before migrating an existing cloud bot; handle that migration separately. This project does not change your server automatically.

Set `TELEGRAM_LOCAL_MODE=1` and the local URLs. `MAX_FILE_SIZE_MB` still applies. The server binary, server data, Telegram API ID/hash and sessions are not included.

## Verification

```powershell
./.venv/Scripts/python.exe -m unittest discover -s tests -v
./.venv/Scripts/python.exe -m pip check
```

Tests cover private access, file types, rejection before download, Telegram RetryAfter without repeated delivered chunks, model fallback, cleanup and FFmpeg. API calls in unit tests are mocked; the FFmpeg test is skipped if it is unavailable.

The [verification report](docs/verification.md) records a separate real Gemini API run with a local Telegram simulator. Delivery through real Telegram on another computer remains unverified.

## Data and license

Only the `.env` next to the script is loaded. The bot does not store names, user IDs, audio or transcripts in a database. Temporary audio is removed after processing; uploaded Gemini files are deleted when possible. Cleanup can fail after process termination or API unavailability. Telegram's and Google's retention policies still apply.

Console logging records stages and error types. Tokens, transcripts and chat IDs are not logged by the code. Runtime files are excluded by `.gitignore`; inspect `git ls-files` before publishing a customized copy.

Application source: [MIT](LICENSE). Dependencies retain their own licenses. This public package excludes credentials and data from the original working directory.
