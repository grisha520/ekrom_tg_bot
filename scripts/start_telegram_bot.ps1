# Telegram bot for managers (long polling on local PC).
# Keep this PC running while the bot is in use.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    Write-Error "Create venv first: pip install -r requirements.txt"
}

if (-not (Test-Path ".\.env")) {
    Write-Error "Create .env from .env.example (TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_IDS, GROQ, EKROM)"
}

$env:PYTHONPATH = "src"
$env:PYTHONIOENCODING = "utf-8"
if (-not $env:PLAYWRIGHT_BROWSERS_PATH) {
    $env:PLAYWRIGHT_BROWSERS_PATH = "$env:LOCALAPPDATA\ms-playwright"
}

# Only one long-polling instance per bot token (else TelegramConflictError).
$existing = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'max_ekrom_bot\.telegram_app' }
if ($existing) {
    Write-Host "Stopping previous bot instance(s)..." -ForegroundColor Yellow
    $existing | ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
}

Write-Host "Starting Telegram bot (Ctrl+C to stop)..." -ForegroundColor Cyan
Write-Host "If api.telegram.org fails: enable VPN or set TELEGRAM_PROXY in .env" -ForegroundColor Yellow
.\.venv\Scripts\python.exe -m max_ekrom_bot.telegram_app
