$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not (Test-Path '.venv')) {
  py -m venv .venv
}
& '.\.venv\Scripts\python.exe' -m pip install --upgrade pip
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt

if (-not (Test-Path '.env')) { Copy-Item '.env.example' '.env' }

Push-Location 'frontend'
npm install
Pop-Location
Write-Host ''
Write-Host 'Setup complete. To start SigmaPilot, run: .\scripts\start_windows.ps1' -ForegroundColor Green
Write-Host 'Optional: edit .env and add LLM_API_KEY / LLM_MODEL to enable hosted AI.'
