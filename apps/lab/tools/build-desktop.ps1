[CmdletBinding()]
param([switch]$SkipBuild)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$labRoot = Split-Path -Parent $PSScriptRoot
$originalLocation = Get-Location
try {
    Set-Location -LiteralPath $labRoot
    $runtime = Join-Path $labRoot 'src-tauri\runtime'
    if (-not (Test-Path -LiteralPath (Join-Path $runtime 'sim-lab-backend.exe') -PathType Leaf)) {
        throw 'Run tools/prepare-backend.ps1 before building the desktop app.'
    }
    if (-not $SkipBuild) {
        & npm exec -- tauri build --no-bundle -- --locked
        if ($LASTEXITCODE -ne 0) { throw "Tauri build failed ($LASTEXITCODE)" }
    }
    $cargoTarget = Join-Path $labRoot 'src-tauri\target'
    if ($env:CARGO_TARGET_DIR) {
        $cargoTarget = if ([System.IO.Path]::IsPathRooted($env:CARGO_TARGET_DIR)) {
            $env:CARGO_TARGET_DIR
        } else {
            Join-Path $labRoot $env:CARGO_TARGET_DIR
        }
        $cargoTarget = [System.IO.Path]::GetFullPath($cargoTarget)
    }
    $release = Join-Path $cargoTarget 'release'
    $executable = Join-Path $release 'dm-mc02-lab.exe'
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        throw "Built desktop executable is missing: $executable"
    }
    $stamp = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 6)
    $destination = Join-Path $labRoot "build\desktop\DM-MC02-Simulator-$stamp"
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    Copy-Item -LiteralPath $executable -Destination (Join-Path $destination 'DM-MC02 Simulator.exe')
    # GNU builds use the WebView2 loader DLL emitted by tauri-build.
    if (Test-Path -LiteralPath (Join-Path $release 'WebView2Loader.dll')) {
        Copy-Item -LiteralPath (Join-Path $release 'WebView2Loader.dll') -Destination $destination
    }
    Copy-Item -LiteralPath $runtime -Destination (Join-Path $destination 'runtime') -Recurse
    Copy-Item -LiteralPath (Join-Path $labRoot 'src') -Destination (Join-Path $destination 'lab') -Recurse
    Copy-Item -LiteralPath (Join-Path $labRoot 'README.md') -Destination $destination
    & node tools/collect-desktop-notices.mjs (Join-Path $destination 'licenses\desktop')
    if ($LASTEXITCODE -ne 0) { throw "Desktop notice collection failed ($LASTEXITCODE)" }
    @'
Double-click DM-MC02 Simulator.exe. Keep runtime/, lab/ and any DLLs beside it.
The app needs Windows WebView2. It owns one simulation backend and stops it on exit.
Desktop licenses: licenses/desktop/; backend: runtime/licenses/; browser: lab/vendor/licenses/.
Corresponding backend source package: see runtime/SOURCE_BUNDLE.txt.
For redistribution include that matching complete source package.
'@ | Set-Content -LiteralPath (Join-Path $destination 'START-HERE.txt') -Encoding utf8
    $destination | Set-Content -LiteralPath (Join-Path $labRoot 'build\desktop\latest.txt') -Encoding utf8
    Write-Host "Desktop app: $(Join-Path $destination 'DM-MC02 Simulator.exe')"
}
finally { Set-Location -LiteralPath $originalLocation }
