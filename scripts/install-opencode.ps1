param([ValidatePattern('^\d+\.\d+\.\d+$')][string]$Version = '1.19.4')
$ErrorActionPreference = 'Stop'
$sourceRoot = Split-Path -Parent $PSScriptRoot
$installRoot = Join-Path $sourceRoot '.tools\opencode'
$npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $npmCommand) { throw 'npm.cmd not found. Install Node.js/npm first: https://nodejs.org/' }
Write-Host "Installing OpenCode $Version into $installRoot"
& $npmCommand.Source install --prefix $installRoot "opencode-ai@$Version" --no-audit --no-fund
if ($LASTEXITCODE -ne 0) { throw 'OpenCode installation failed. Check the npm output and network connection.' }
$executable = Join-Path $installRoot 'node_modules\opencode-ai\bin\opencode.exe'
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) { throw "Executable not found: $executable" }
Write-Host "OpenCode path: $executable"
Write-Host 'Restart RTL Lab or set this executable path in API/model settings.'
