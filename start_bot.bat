@echo off
setlocal
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Install dependencies as described in README.md first.
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" "%~dp0my_telegram_bot.py" %*
exit /b %ERRORLEVEL%
