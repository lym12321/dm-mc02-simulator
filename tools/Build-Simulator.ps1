[CmdletBinding()]
param(
    [string]$MsysRoot = $env:MSYS2_ROOT,
    [string]$QemuPath,
    [string]$Python = 'python',
    [string]$DesktopTargetDir,
    [switch]$SkipQemuBuild,
    [switch]$Development,
    [ValidateRange(1, 64)][int]$Jobs = 4
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$simulatorRoot = Split-Path -Parent $PSScriptRoot
$originalLocation = Get-Location
$originalCargoTarget = $env:CARGO_TARGET_DIR
try {
    . (Join-Path $PSScriptRoot 'Enter-Dev.ps1') -MsysRoot $MsysRoot
    Set-Location -LiteralPath $simulatorRoot
    $engineBuild = Join-Path $simulatorRoot 'qemu\upstream\scripts\build-dm-mc02.ps1'
    if (-not (Test-Path -LiteralPath $engineBuild)) {
        throw 'Initialize the pinned engine first: git submodule update --init qemu/upstream'
    }
    if (-not $QemuPath) {
        $QemuPath = Join-Path $simulatorRoot 'build\qemu\qemu-system-arm.exe'
    }
    if (-not $SkipQemuBuild) {
        & $engineBuild -MsysRoot $env:MSYS2_ROOT -BuildDir (Split-Path -Parent $QemuPath) -Jobs $Jobs
    }
    $prepare = @{
        BackendRoot = $simulatorRoot
        MsysRoot = $env:MSYS2_ROOT
        QemuPath = $QemuPath
        Python = $Python
        Development = $Development
    }
    & (Join-Path $simulatorRoot 'apps\lab\tools\prepare-backend.ps1') @prepare
    & npm --prefix apps/lab ci
    if ($LASTEXITCODE -ne 0) { throw "npm ci failed ($LASTEXITCODE)" }
    if ($DesktopTargetDir) {
        $env:CARGO_TARGET_DIR = [System.IO.Path]::GetFullPath($DesktopTargetDir)
    }
    & (Join-Path $simulatorRoot 'apps\lab\tools\build-desktop.ps1')
}
finally {
    if ($null -eq $originalCargoTarget) {
        Remove-Item Env:CARGO_TARGET_DIR -ErrorAction SilentlyContinue
    } else { $env:CARGO_TARGET_DIR = $originalCargoTarget }
    Set-Location -LiteralPath $originalLocation
}
