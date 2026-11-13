# Create the environment, install ordinary dependencies, and register bundled CLI.
param([switch]$DataOnly)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    & py -3.11 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 x64 is required.' }
}
& $taskPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Base dependency installation failed.' }
& $taskPython -m pip install --no-build-isolation --no-deps -e .\vendor\wechat-cli
if ($LASTEXITCODE -ne 0) { throw 'Bundled CLI registration failed.' }
if (-not $DataOnly) {
    & $taskPython -m pip install -r requirements-autoreply.txt
    if ($LASTEXITCODE -ne 0) { throw 'UIA dependency installation failed.' }
}
Write-Host 'Ready. Local CLI: .\.venv\Scripts\wechat-cli.exe --help'
Write-Host 'Fill your own configuration before exporting or running replies.'
