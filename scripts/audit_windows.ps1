# Read-only inventory. Never initialize CAN, attach USB or start a camera stream.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding
$usbipd = Join-Path $env:ProgramFiles 'usbipd-win\usbipd.exe'
$apps = @(Get-ItemProperty 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue |
    Where-Object { $_.DisplayName -match 'PEAK|PCAN|usbipd|Orbbec|Visual Studio Code' } |
    Select-Object DisplayName, DisplayVersion, InstallLocation)
$dlls = @(foreach ($dll in @('System32\PCANBasic.dll', 'SysWOW64\PCANBasic.dll')) {
    $path = Join-Path $env:windir $dll
    if (Test-Path $path) {
        $item = Get-Item $path
        @{ path = $path; version = $item.VersionInfo.FileVersion; bytes = $item.Length }
    }
})
$devices = @(Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue |
    Where-Object { $_.InstanceId -like 'USB\*' -and
        ($_.FriendlyName -match 'Orbbec|Gemini|PCAN|PEAK|CANable' -or
         $_.InstanceId -match 'VID_2BC5|VID_0C72') } |
    Select-Object FriendlyName, InstanceId, Status)
$viewerRoot = 'D:\reBot-tools\OrbbecViewer-v2.7.6'
$viewers = @(if (Test-Path $viewerRoot) {
    Get-ChildItem $viewerRoot -Filter OrbbecViewer.exe -Recurse |
    ForEach-Object { @{ path = $_.FullName; version = $_.VersionInfo.FileVersion;
        sha256 = (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower() } }
})
$extensions = @()
$extensionPath = Join-Path $env:USERPROFILE '.vscode\extensions\extensions.json'
if (Test-Path $extensionPath) {
    $extensions = @(foreach ($extension in (Get-Content $extensionPath -Raw | ConvertFrom-Json)) {
        @{ id = $extension.identifier.id; version = $extension.version }
    })
}
$service = Get-Service usbipd -ErrorAction SilentlyContinue
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class RebotLibraryAudit {
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern IntPtr LoadLibraryW(string path);
    [DllImport("kernel32.dll")]
    public static extern bool FreeLibrary(IntPtr module);
}
'@
$pcanHandle = [RebotLibraryAudit]::LoadLibraryW((Join-Path $env:windir 'System32\PCANBasic.dll'))
$pcanLoadError = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
$pcanLoadable = $pcanHandle -ne [IntPtr]::Zero
if ($pcanLoadable) { [void][RebotLibraryAudit]::FreeLibrary($pcanHandle) }
@{
    windows_version = [Environment]::OSVersion.Version.ToString()
    powershell_version = $PSVersionTable.PSVersion.ToString()
    usbipd_installed = (Test-Path $usbipd)
    usbipd_version = $(if (Test-Path $usbipd) { (& $usbipd --version | Out-String).Trim() })
    usbipd_service = $(if ($service) { $service.Status.ToString() })
    usbipd_devices = $(if (Test-Path $usbipd) { (& $usbipd list | Out-String).Trim() })
    pcan_basic_dll = $dlls
    pcan_x64_loadable = $pcanLoadable
    pcan_load_error = $(if (-not $pcanLoadable) { $pcanLoadError })
    relevant_usb = $devices
    relevant_apps = $apps
    orbbec_viewer = $viewers
    vscode_extensions = $extensions
    motor_commands_sent = $false
} | ConvertTo-Json -Depth 7
