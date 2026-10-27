param(
    [ValidateRange(1,65535)][int]$Port = 8765,
    [string]$Python = ''
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonPrefix = @()
if ($Python) {
    $pythonCommand = $Python
} elseif (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.venv\Scripts\python.exe')) {
    $pythonCommand = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $pythonCommand = (Get-Command python).Source
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $pythonCommand = (Get-Command py).Source
    $pythonPrefix = @('-3')
} else {
    throw 'Python not found. Install Python 3.13 or pass -Python C:\path\to\python.exe.'
}
& $pythonCommand @pythonPrefix -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.10+ is required; Python 3.13 is recommended.' }
Write-Host "RTL Lab: http://127.0.0.1:$Port/chat"
Write-Host 'Keep this terminal open. Press Ctrl+C to stop the server.'
$previousUtf8 = $env:PYTHONUTF8
try {
    $env:PYTHONUTF8 = '1'
    & $pythonCommand @pythonPrefix -u (Join-Path $PSScriptRoot 'app.py') serve --port $Port
    $serverExit = $LASTEXITCODE
} finally {
    $env:PYTHONUTF8 = $previousUtf8
}
exit $serverExit
