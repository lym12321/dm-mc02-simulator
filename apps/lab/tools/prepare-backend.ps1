[CmdletBinding(DefaultParameterSetName = 'Checkout')]
param(
    [Parameter(ParameterSetName = 'Checkout')]
    [string]$BackendRoot = (Join-Path $PSScriptRoot '..\..\..'),
    [Parameter(Mandatory, ParameterSetName = 'Package')]
    [string]$RuntimeDir,
    [Parameter(ParameterSetName = 'Checkout')]
    [string]$MsysRoot = $env:MSYS2_ROOT,
    [Parameter(ParameterSetName = 'Checkout')]
    [string]$QemuPath,
    [Parameter(ParameterSetName = 'Checkout')]
    [string]$SimPath,
    [Parameter(ParameterSetName = 'Checkout')]
    [string]$DesktopBackendPath,
    [Parameter(ParameterSetName = 'Checkout')]
    [string]$Python = 'python',
    [Parameter(ParameterSetName = 'Checkout')]
    [switch]$Development,
    [Parameter(ParameterSetName = 'Checkout')]
    [switch]$NoBuild
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$labRoot = Split-Path -Parent $PSScriptRoot
$buildRoot = Join-Path $labRoot 'build'
$operationId = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 8)
$originalPath = $env:Path
$originalQemu = $env:QEMU_SYSTEM_ARM
$originalLocation = Get-Location

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed ($LASTEXITCODE): $Program $($Arguments -join ' ')"
    }
}

try {
    New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null
    if ($PSCmdlet.ParameterSetName -eq 'Checkout') {
        $BackendRoot = (Resolve-Path -LiteralPath $BackendRoot).Path
        if (-not $MsysRoot) {
            $pacman = Get-Command pacman.exe -ErrorAction SilentlyContinue
            if ($pacman) { $MsysRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $pacman.Source)) }
        }
        if (-not $MsysRoot) {
            throw 'Supply -MsysRoot or set MSYS2_ROOT to the MSYS2 installation used to build QEMU.'
        }
        $MsysRoot = (Resolve-Path -LiteralPath $MsysRoot).Path
        $dllDirectory = Join-Path $MsysRoot 'mingw64\bin'
        $cargoDirectory = Join-Path $env:USERPROFILE '.cargo\bin'
        $env:Path = "$cargoDirectory;$dllDirectory;$(Join-Path $MsysRoot 'usr\bin');$originalPath"
        if (-not $QemuPath) { $QemuPath = Join-Path $BackendRoot 'build\qemu\qemu-system-arm.exe' }
        $cargoTarget = Join-Path $BackendRoot 'target'
        if ($env:CARGO_TARGET_DIR) {
            $cargoTarget = if ([System.IO.Path]::IsPathRooted($env:CARGO_TARGET_DIR)) {
                $env:CARGO_TARGET_DIR
            } else {
                Join-Path $BackendRoot $env:CARGO_TARGET_DIR
            }
            $cargoTarget = [System.IO.Path]::GetFullPath($cargoTarget)
        }
        if (-not $SimPath) { $SimPath = Join-Path $cargoTarget 'release\sim.exe' }
        if (-not $DesktopBackendPath) { $DesktopBackendPath = Join-Path $cargoTarget 'release\sim-lab-backend.exe' }
        $QemuPath = (Resolve-Path -LiteralPath $QemuPath).Path
        $packager = Join-Path $BackendRoot 'tools\package-qemu.py'
        if (-not (Test-Path -LiteralPath $packager -PathType Leaf)) {
            throw "Backend packager is missing: $packager"
        }
        Set-Location -LiteralPath $BackendRoot
        if (-not $NoBuild) {
            Invoke-Checked -Program 'cargo' -Arguments @('build', '--release', '--locked', '-p', 'sim-host', '--features', 'windows-lab,desktop-backend', '--bins')
        }
        $SimPath = (Resolve-Path -LiteralPath $SimPath).Path
        $DesktopBackendPath = (Resolve-Path -LiteralPath $DesktopBackendPath).Path
        Invoke-Checked -Program $DesktopBackendPath -Arguments @('--help')
        $RuntimeDir = Join-Path $buildRoot "backend-packages\$operationId"
        $packageArguments = @($packager, '--qemu', $QemuPath, '--sim', $SimPath,
            '--output', $RuntimeDir, '--dll-dir', $dllDirectory, '--lab',
            '--desktop-backend', $DesktopBackendPath)
        if ($Development) { $packageArguments += '--development-worktree' }
        Invoke-Checked -Program $Python -Arguments $packageArguments
    }

    $RuntimeDir = (Resolve-Path -LiteralPath $RuntimeDir).Path
    foreach ($required in @('sim.exe', 'sim-lab-backend.exe', 'bin\qemu-system-arm.exe', 'provenance.json', 'licenses', 'sources')) {
        if (-not (Test-Path -LiteralPath (Join-Path $RuntimeDir $required))) {
            throw "The complete backend package is missing $required`: $RuntimeDir"
        }
    }

    # Verify the runtime without finding a DLL or another QEMU in the development PATH.
    $env:Path = "$(Join-Path $env:SystemRoot 'System32');$env:SystemRoot"
    Remove-Item Env:QEMU_SYSTEM_ARM -ErrorAction SilentlyContinue
    Set-Location -LiteralPath $buildRoot
    Invoke-Checked -Program (Join-Path $RuntimeDir 'sim-lab-backend.exe') -Arguments @('--help')
    $machineHelp = & (Join-Path $RuntimeDir 'bin\qemu-system-arm.exe') -machine help
    if ($LASTEXITCODE -ne 0 -or -not ($machineHelp -match '^dm-mc02\s')) {
        throw 'The packaged QEMU could not expose the dm-mc02 machine with a minimal PATH.'
    }

    $stagedRuntime = Join-Path $buildRoot "runtime-stage-$operationId"
    New-Item -ItemType Directory -Path $stagedRuntime | Out-Null
    Get-ChildItem -LiteralPath $RuntimeDir -Force | Where-Object Name -ne 'sources' |
        Copy-Item -Destination $stagedRuntime -Recurse
    @"
Complete backend package: $RuntimeDir
Corresponding sources: $(Join-Path $RuntimeDir 'sources')

The original provenance.json describes this complete package. Its sources/ paths
resolve from the package directory above. The Tauri resources contain the runtime
and licenses. Distribute the matching sources separately with a desktop release;
do not present this development snapshot as the committed HEAD source.
"@ | Set-Content -LiteralPath (Join-Path $stagedRuntime 'SOURCE_BUNDLE.txt') -Encoding utf8

    $runtimeTarget = Join-Path $labRoot 'src-tauri\runtime'
    if (Test-Path -LiteralPath $runtimeTarget) {
        # Keep the previous prepared runtime recoverable; never merge stale DLLs.
        Move-Item -LiteralPath $runtimeTarget -Destination (Join-Path $buildRoot "runtime-previous-$operationId")
    }
    Move-Item -LiteralPath $stagedRuntime -Destination $runtimeTarget
    [ordered]@{
        schema = 'dm-mc02-lab/backend-package/v1'
        runtimeDir = $runtimeTarget
        packageDir = $RuntimeDir
        sourcesDir = Join-Path $RuntimeDir 'sources'
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $buildRoot 'backend-package.json') -Encoding utf8
    Write-Host "Tauri runtime: $runtimeTarget"
    Write-Host "Complete package and corresponding sources: $RuntimeDir"
}
finally {
    $env:Path = $originalPath
    if ($null -eq $originalQemu) {
        Remove-Item Env:QEMU_SYSTEM_ARM -ErrorAction SilentlyContinue
    }
    else { $env:QEMU_SYSTEM_ARM = $originalQemu }
    Set-Location -LiteralPath $originalLocation
}
