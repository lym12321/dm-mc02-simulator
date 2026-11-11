[CmdletBinding()]
param([string]$MsysRoot = $env:MSYS2_ROOT)

$simulatorRoot = Split-Path -Parent $PSScriptRoot
if (-not $MsysRoot) {
    foreach ($candidate in @('D:\Environment\msys64', 'C:\msys64')) {
        if (Test-Path -LiteralPath (Join-Path $candidate 'mingw64\bin\gcc.exe')) {
            $MsysRoot = $candidate
            break
        }
    }
}
if (-not $MsysRoot -or -not (Test-Path -LiteralPath (Join-Path $MsysRoot 'mingw64\bin\gcc.exe'))) {
    throw 'Supply -MsysRoot pointing to MSYS2 with its MINGW64 compiler installed.'
}
$env:MSYS2_ROOT = (Resolve-Path -LiteralPath $MsysRoot).Path
$simulatorPaths = @(
    (Join-Path $env:USERPROFILE '.cargo\bin'),
    (Join-Path $env:MSYS2_ROOT 'mingw64\bin'),
    (Join-Path $env:MSYS2_ROOT 'usr\bin'),
    (Join-Path $simulatorRoot '.venv\Scripts'),
    (Join-Path $simulatorRoot 'target\release')
)
$env:Path = (($simulatorPaths + ($env:Path -split ';')) | Select-Object -Unique) -join ';'
$env:QEMU_SYSTEM_ARM = Join-Path $simulatorRoot 'build\qemu\qemu-system-arm.exe'
Write-Host "Simulator: $simulatorRoot"
Write-Host "MSYS2 MINGW64: $env:MSYS2_ROOT"
