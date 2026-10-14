$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { throw 'Virtual environment missing. Run .\scripts\setup_windows.ps1 first.' }
if (-not (Test-Path (Join-Path $Root 'frontend\node_modules'))) { throw 'Frontend dependencies missing. Run .\scripts\setup_windows.ps1 first.' }
$BackendCommand = "Set-Location '$Root'; & '$Python' -m uvicorn app.main:app --app-dir '$Root\backend' --host 127.0.0.1 --port 8000 --reload"
$FrontendCommand = "Set-Location '$Root\frontend'; npm run dev"
Start-Process powershell -ArgumentList @('-NoExit', '-ExecutionPolicy', 'Bypass', '-Command', $BackendCommand)
Start-Process powershell -ArgumentList @('-NoExit', '-ExecutionPolicy', 'Bypass', '-Command', $FrontendCommand)
Start-Sleep -Seconds 2
Write-Host 'SigmaPilot is starting.' -ForegroundColor Green
Write-Host 'Frontend: http://127.0.0.1:5173'
Write-Host 'Backend API: http://127.0.0.1:8000/api/health'
Write-Host 'API docs: http://127.0.0.1:8000/docs'
