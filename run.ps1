param([switch]$Check)
$ErrorActionPreference = 'Stop'
$pythonPath = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Install dependencies as described in README.md first.' }
$taskArguments = @((Join-Path $PSScriptRoot 'my_telegram_bot.py'))
if ($Check) { $taskArguments += '--check' }
& $pythonPath @taskArguments
exit $LASTEXITCODE
