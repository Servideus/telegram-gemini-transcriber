# Telegram Gemini Transcriber

A personal Telegram bot that turns Russian voice messages and audio files into edited text using Gemini. It accepts one configured private chat, handles long transcripts and temporary API failures, and removes temporary audio after processing. No database, user profiles, usage statistics, admin commands or daily quotas.

This is a standalone source package. Bring your own Telegram bot token and Gemini API key. Audio is sent to Google; Gemini may consume paid quota. The prompt corrects punctuation and removes fillers, so the result is not a verbatim transcript.

## Установка

Проверено на Windows 11 с Python 3.12.3. Сам бот не использует Windows API; Linux отдельно не проверен. FFmpeg нужен для преобразования форматов при ошибке прямого распознавания.

Из каталога проекта:

```powershell
py -3.12 -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements-lock.txt
Copy-Item .env.example .env
```

В `.env` укажите токен собственного бота, созданного через [BotFather](https://t.me/BotFather), и [ключ Gemini API](https://aistudio.google.com/apikey). Не используйте один bot token одновременно в двух polling-процессах.

```powershell
./run.ps1 -Check
./run.ps1
```

`-Check` проверяет конфигурацию без сетевых запросов; он не подтверждает валидность ключей. Альтернативный Windows-запуск: `start_bot.bat`. На Linux: `.venv/bin/python my_telegram_bot.py` после создания venv и установки зависимостей.

## Настройка личного чата

1. При первом запуске оставьте `TELEGRAM_CHAT_ID` пустым. Отправьте `/start` своему боту в личном чате: он покажет ID этого чата. Распознавание пока отключено.
2. Запишите показанный ID в `.env` и перезапустите бота. После этого он обрабатывает аудио только из этого личного чата; сообщения других чатов и групп игнорируются.
3. Отправьте голосовое сообщение, аудиозапись или аудиофайл как документ. Бот заменит «Обрабатываю…» на текст; длинный ответ отправит частями.

Это одна настройка доступа. У бота нет БД, из команд доступна только `/start`.

## Параметры

| Переменная | Назначение |
|---|---|
| `GEMINI_MODELS` | Модели через запятую в порядке приоритета; по умолчанию `gemini-flash-latest,gemini-flash-lite-latest`, как в исходной локальной копии |
| `REQUEST_TIMEOUT` | Общий таймаут скачивания и распознавания, по умолчанию 420 секунд |
| `MAX_FILE_SIZE_MB` | Максимальный размер файла, по умолчанию 50 MB; облачный Telegram ограничивает скачивание 20 MB |
| `FFMPEG_EXE` | `ffmpeg` из PATH или полный путь к исполняемому файлу |
| `TELEGRAM_LOCAL_MODE` | `1` для уже настроенного локального Telegram Bot API |
| `TELEGRAM_BASE_URL` | Например `http://127.0.0.1:8081/bot` для локального сервера |
| `TELEGRAM_BASE_FILE_URL` | При необходимости `http://127.0.0.1:8081/file/bot` |

Голосовые OGG, Telegram audio и аудиодокументы поддерживаются одним обработчиком. Распознаются расширения `.ogg`, `.oga`, `.opus`, `.mp3`, `.m4a`, `.aac`, `.wav`, `.flac`, `.wma`, `.aiff`, `.aif`, `.amr`; это не гарантия поддержки каждого кодека Gemini или установленным FFmpeg.

При 429 и временных серверных ошибках есть ограниченные повторы. Если модель недоступна, бот пробует следующую. Ошибка ключа не запускает резервные запросы. Настройки thinking оставлены модели, поскольку их поддержка различается.

## Файлы больше 20 MB

По умолчанию используется облачный Bot API с [ограничением скачивания 20 MB](https://core.telegram.org/bots/api#getfile). Для более крупных файлов нужен отдельно установленный [локальный Telegram Bot API](https://github.com/tdlib/telegram-bot-api), работающий с `--local`. Перед переключением уже работающего облачного бота Telegram требует вызова `logOut`; выполняйте миграцию отдельно от обычного запуска. Этот проект не меняет сервер автоматически.

Укажите `TELEGRAM_LOCAL_MODE=1` и локальные URL. Бот по-прежнему соблюдает `MAX_FILE_SIZE_MB`. Серверный каталог, бинарник Bot API, Telegram API ID/hash и данные сессии не входят в этот пакет.

## Проверки

```powershell
./.venv/Scripts/python.exe -m unittest discover -s tests -v
./.venv/Scripts/python.exe -m pip check
```

Тесты проверяют личный доступ, типы файлов, отказ до скачивания при превышении размера, повтор Telegram RetryAfter без дублирования уже отправленных частей, резервные модели, очистку и FFmpeg. Проверки без сетевых запросов; тест FFmpeg пропускается, если он не установлен.

[Протокол проверки](docs/verification.md) описывает отдельный запуск с настоящим Gemini API и локальной имитацией Telegram. Работа через реальный Telegram на другом компьютере ещё не проверена.

## Данные и лицензия

`.env` читается только рядом со скриптом. Бот не сохраняет имена, идентификаторы пользователей, аудио или транскрипции в БД. Временные файлы удаляются при завершении обработки; загруженный файл Gemini удаляется по возможности. При аварийном завершении процесса или недоступности API очистка может не выполниться. Это не меняет правила хранения данных Telegram и Google.

Журнал в консоли содержит этапы и типы ошибок; код не выводит токены, текст записи или ID чата в журнал. Служебные файлы исключены из Git по правилам `.gitignore`. Перед публикацией проверьте фактический состав `git ls-files`.

Исходники приложения: [MIT](LICENSE). Зависимости сохраняют собственные лицензии. Подготовленная версия не содержит ключей и данных исходной рабочей папки.
