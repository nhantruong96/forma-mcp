# Install Forma MCP. Prefers `uv sync`; falls back to venv + pip only when uv is missing.
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -Templates

param(
    [switch]$Templates   # also install openpyxl for scripts\gen_acc_naming_template.py
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (Get-Command uv -ErrorAction SilentlyContinue) {
    Write-Host '==> uv sync' -ForegroundColor Cyan
    if ($Templates) { uv sync --extra templates } else { uv sync }
    $python = Join-Path $root '.venv\Scripts\python.exe'
} else {
    Write-Host '==> uv not found, falling back to venv + pip' -ForegroundColor Yellow
    if (-not (Test-Path '.venv')) { py -3 -m venv .venv }
    $python = Join-Path $root '.venv\Scripts\python.exe'
    & $python -m pip install --upgrade pip
    & $python -m pip install -e .
    if ($Templates) { & $python -m pip install 'openpyxl>=3.1' }
}

if (-not (Test-Path '.env')) {
    Copy-Item '.env.example' '.env'
    Write-Host '==> created .env from .env.example - fill in APS_CLIENT_ID' -ForegroundColor Yellow
}

Write-Host '==> selfcheck' -ForegroundColor Cyan
& $python -m forma_mcp --selfcheck

Write-Host ''
Write-Host "Claude Desktop command: $python" -ForegroundColor Green
Write-Host 'Claude Desktop args:    ["-m", "forma_mcp"]' -ForegroundColor Green
