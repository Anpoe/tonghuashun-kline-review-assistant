param(
    [switch]$SkipApplicationBuild
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$version = "1.0.1"
$releaseDir = Join-Path $root "release"
$appDir = Join-Path $root "dist\KlineReviewAssistant"
$portableName = "KlineReviewAssistant-$version-portable"
$portableStage = Join-Path $releaseDir $portableName
$portableZip = Join-Path $releaseDir ($portableName + ".zip")

if (-not $SkipApplicationBuild) {
    python build_icon.py
    if ($LASTEXITCODE -ne 0) { throw "Icon build failed." }

    python -m PyInstaller --noconfirm --clean KlineReviewAssistant.spec
    if ($LASTEXITCODE -ne 0) { throw "Application build failed." }
}

if (-not (Test-Path -LiteralPath $appDir -PathType Container)) {
    throw "Application output was not created: $appDir"
}

# Public builds must never contain the developer's personal application config.
# OCR model packages have their own unrelated files with the same basename.
$personalConfigs = @(
    (Join-Path $appDir "config.yaml"),
    (Join-Path $appDir "_internal\config.yaml")
) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf }
if ($personalConfigs) {
    throw "Refusing to publish a build containing a personal application config: $($personalConfigs -join ', ')"
}
Copy-Item -LiteralPath (Join-Path $root "README.md") -Destination (Join-Path $appDir "README.md") -Force
Copy-Item -LiteralPath (Join-Path $root "QUICKSTART.txt") -Destination (Join-Path $appDir "QUICKSTART.txt") -Force

New-Item -ItemType Directory -Path $releaseDir -Force | Out-Null
if (Test-Path -LiteralPath $portableStage) {
    Remove-Item -LiteralPath $portableStage -Recurse -Force
}
if (Test-Path -LiteralPath $portableZip) {
    Remove-Item -LiteralPath $portableZip -Force
}
Copy-Item -LiteralPath $appDir -Destination $portableStage -Recurse
Compress-Archive -LiteralPath $portableStage -DestinationPath $portableZip -CompressionLevel Optimal
Remove-Item -LiteralPath $portableStage -Recurse -Force

$isccCandidates = @(
    "$env:ProgramFiles(x86)\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
)
$iscc = $isccCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if ($iscc) {
    $issPath = Join-Path $root "installer.iss"
    $issText = [System.IO.File]::ReadAllText($issPath, [System.Text.Encoding]::UTF8)
    [System.IO.File]::WriteAllText($issPath, $issText, [System.Text.UTF8Encoding]::new($true))
    & $iscc $issPath
    if ($LASTEXITCODE -ne 0) { throw "Installer build failed." }
} else {
    Write-Warning "Inno Setup 6 was not found. Portable ZIP was created; installer was skipped."
}

$artifacts = Get-ChildItem -LiteralPath $releaseDir -File |
    Where-Object { $_.Name -like "KlineReviewAssistant-$version-*" -or $_.Name -eq "KlineReviewAssistant-Setup-$version.exe" }
$checksums = foreach ($artifact in $artifacts) {
    $hash = Get-FileHash -LiteralPath $artifact.FullName -Algorithm SHA256
    "$($hash.Hash)  $($artifact.Name)"
}
$checksums | Set-Content -LiteralPath (Join-Path $releaseDir "SHA256SUMS.txt") -Encoding ascii

Get-ChildItem -LiteralPath $releaseDir -File | Select-Object Name, Length, LastWriteTime
