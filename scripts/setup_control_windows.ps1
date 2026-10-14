$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$controlPython = Join-Path $projectRoot '.venv-control-win\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $controlPython)) {
    python -m venv (Join-Path $projectRoot '.venv-control-win')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create control environment.' }
}
& $controlPython -m pip install -r (Join-Path $projectRoot 'requirements\robot-sdk.txt')
if ($LASTEXITCODE -ne 0) { throw 'MotorBridge installation failed.' }
& $controlPython -m pip install -e $projectRoot
if ($LASTEXITCODE -ne 0) { throw 'Project installation failed.' }
Write-Output 'Software installed. Run scripts/control_windows.ps1 doctor; hardware is checked separately.'
