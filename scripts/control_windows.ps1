param(
    [ValidateSet('doctor', 'probe', 'gateway')]
    [string]$Action = 'doctor',
    [string]$Channel
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$controlPython = Join-Path $projectRoot '.venv-control-win\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $controlPython)) {
    throw 'Run scripts/setup_control_windows.ps1 first.'
}
Set-Location -LiteralPath $projectRoot
switch ($Action) {
    'doctor' {
        $doctorArgs = @('scripts/doctor_control.py', '--output', 'artifacts/control-windows.json')
        if ($Channel) { $doctorArgs += @('--channel', $Channel) }
        & $controlPython @doctorArgs
    }
    'probe' {
        if (-not $Channel) { throw 'Specify the confirmed channel, e.g. -Channel can0@1000000.' }
        & $controlPython -m rebot_tea.connection --channel $Channel --output artifacts/motors-windows.json
    }
    'gateway' {
        if (-not $Channel) { throw 'Specify the confirmed channel, e.g. -Channel can0@1000000.' }
        & $controlPython -m motorbridge.gateway --bind 127.0.0.1:9002 --vendor robstride --channel $Channel
    }
}
exit $LASTEXITCODE
