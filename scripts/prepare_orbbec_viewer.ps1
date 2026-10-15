# Portable official viewer; no administrator rights, firmware update or GUI launch.
param([string]$Destination = 'D:\reBot-tools\OrbbecViewer-v2.7.6')
$ErrorActionPreference = 'Stop'
if (Test-Path $Destination) { throw "Destination already exists; preserve and inspect: $Destination" }
$viewerUrl = 'https://github.com/orbbec/OrbbecSDK_v2/releases/download/v2.7.6/OrbbecViewer_v2.7.6_202602022045_20730ef_win_x64.zip'
$expectedHash = '7e337bc4a2decc5d39b5f554b75293e56dd95c2148227b3c960d53fd023fb9ff'
$viewerArchive = Join-Path ([IO.Path]::GetTempPath()) ('rebot-viewer-' + [guid]::NewGuid() + '.zip')
try {
    Invoke-WebRequest -UseBasicParsing -Uri $viewerUrl -OutFile $viewerArchive
    if ((Get-FileHash $viewerArchive -Algorithm SHA256).Hash.ToLower() -ne $expectedHash) {
        throw 'Downloaded archive differs from the verified project copy.'
    }
    Expand-Archive -LiteralPath $viewerArchive -DestinationPath $Destination
    Write-Output "Prepared $Destination\OrbbecViewer.exe; open it only for the camera check."
} finally {
    if (Test-Path $viewerArchive) { Remove-Item -LiteralPath $viewerArchive }
}
