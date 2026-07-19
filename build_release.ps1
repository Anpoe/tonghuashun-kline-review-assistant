param(
    [switch]$SkipApplicationBuild,
    [switch]$KeepBuildArtifacts
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$version = "1.1.1"
$buildDir = Join-Path $root "build"
$distDir = Join-Path $root "dist"
$releaseDir = Join-Path $root "release"
$appDir = Join-Path $distDir "KlineReviewAssistant"
$installerName = "KlineReviewAssistant-Setup-$version.exe"
$installerPath = Join-Path $releaseDir $installerName

function Assert-WorkspacePath([string]$Path) {
    $resolvedRoot = [System.IO.Path]::GetFullPath($root).TrimEnd('\') + '\'
    $resolvedPath = [System.IO.Path]::GetFullPath($Path)
    if (-not $resolvedPath.StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean a path outside the project: $resolvedPath"
    }
}

foreach ($path in @($buildDir, $distDir, $releaseDir)) {
    Assert-WorkspacePath $path
}

if (-not $SkipApplicationBuild) {
    foreach ($path in @($buildDir, $distDir)) {
        if (Test-Path -LiteralPath $path) {
            Remove-Item -LiteralPath $path -Recurse -Force
        }
    }

    python build_icon.py
    if ($LASTEXITCODE -ne 0) { throw "Icon build failed." }

    python -m PyInstaller --noconfirm --clean KlineReviewAssistant.spec
    if ($LASTEXITCODE -ne 0) { throw "Application build failed." }
}

if (-not (Test-Path -LiteralPath $appDir -PathType Container)) {
    throw "Application output was not created: $appDir"
}

$personalConfigs = @(
    (Join-Path $appDir "config.yaml"),
    (Join-Path $appDir "_internal\config.yaml")
) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf }
if ($personalConfigs) {
    throw "Refusing to publish a build containing a personal application config: $($personalConfigs -join ', ')"
}

Copy-Item -LiteralPath (Join-Path $root "README.md") -Destination (Join-Path $appDir "README.md") -Force
Copy-Item -LiteralPath (Join-Path $root "QUICKSTART.txt") -Destination (Join-Path $appDir "QUICKSTART.txt") -Force

python smoke_test_release.py
if ($LASTEXITCODE -ne 0) { throw "Release smoke test failed." }

$isccCandidates = @(
    "$env:ProgramFiles(x86)\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
)
$iscc = $isccCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $iscc) {
    throw "Inno Setup 6 was not found. It is required to create the GitHub installer."
}

if (Test-Path -LiteralPath $releaseDir) {
    Remove-Item -LiteralPath $releaseDir -Recurse -Force
}
New-Item -ItemType Directory -Path $releaseDir | Out-Null

$issPath = Join-Path $root "installer.iss"
$issText = [System.IO.File]::ReadAllText($issPath, [System.Text.Encoding]::UTF8)
[System.IO.File]::WriteAllText($issPath, $issText, [System.Text.UTF8Encoding]::new($true))
& $iscc $issPath
if ($LASTEXITCODE -ne 0) { throw "Installer build failed." }
if (-not (Test-Path -LiteralPath $installerPath -PathType Leaf)) {
    throw "Installer output was not created: $installerPath"
}

$hash = Get-FileHash -LiteralPath $installerPath -Algorithm SHA256
"$($hash.Hash)  $installerName" | Set-Content -LiteralPath (Join-Path $releaseDir "SHA256SUMS.txt") -Encoding ascii

Get-ChildItem -LiteralPath $releaseDir -File | Select-Object Name, Length, LastWriteTime

if (-not $KeepBuildArtifacts) {
    foreach ($path in @($buildDir, $distDir)) {
        if (Test-Path -LiteralPath $path) {
            Remove-Item -LiteralPath $path -Recurse -Force
        }
    }
}
