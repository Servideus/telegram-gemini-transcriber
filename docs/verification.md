# Проверка личного Telegram-бота

Дата: 2026-10-03. Исходная папка `Gemini` сохранена без изменений. Новая копия использует три прямые зависимости вместо SQLite и tzdata.

## Выполнено

1. Создано новое Python 3.12.3 venv; зависимости установлены из `requirements.txt`. `pip check` прошёл. Проверенные версии всех зависимостей записаны в `requirements-lock.txt`.
2. 19 автоматических тестов прошли. FFmpeg реально преобразовал stereo 44,1 кГц в mono PCM 16 bit 16 кГц. Остальные API-вызовы в unit-тестах подменены.
3. Метаданные моделей `gemini-flash-latest`, `gemini-flash-lite-latest` и `gemini-2.5-flash-lite` доступны с существующим ключом. Ключ использован только при проверке, в новую копию не записан.
4. Проверен `run.ps1` из другого рабочего каталога: реальный PTB polling получил синтетический Telegram voice update через локальный HTTP-сервер, скачал OGG, загрузил его в настоящий Gemini Files API и отредактировал ответ через Bot API.
5. Вся тестовая цепочка заняла 16,84 секунды. После временной ошибки Gemini 503 повтор завершился успешно на `gemini-flash-latest`. Основной текст синтетической русской фразы совпал, название VoicePaste распознано как «Voicey Paste». Это один запрос, не оценка точности или средней скорости.

## Границы проверки

Telegram-сервер был локальной тестовой имитацией. Сообщения реальным пользователям не отправлялись; существующий Telegram polling и удалённый production-бот не менялись. Реальная доставка через Telegram, локальный Bot API с файлами больше 20 MB и Linux пока не проверены.

Для проверки собственного экземпляра заполните свои ключи, выполните настройку `/start` и отправьте короткое голосовое сообщение. Проверяйте новую копию с отдельным bot token либо предварительно остановите прежний polling того же бота.

В чистом пакете отсутствуют рабочий `.env`, базы, логи, временные записи и данные локального Telegram Bot API. Исходная рабочая копия остаётся способом возврата.

## 2026-10-04 model fallback update

Four models now run in the agreed order. Every error or empty response advances once; generation retries are disabled. Unit tests cover authentication/quota/server errors, timeout, empty responses, Transcribe REST verbatim parsing, audio cleanup and Telegram delivery. The production Oracle bot retains its separate quota/statistics and local Bot API implementation.
