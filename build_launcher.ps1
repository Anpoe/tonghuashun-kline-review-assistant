$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$source = Join-Path $root "launcher\KlineReviewLauncher.cs"
$manifest = Join-Path $root "launcher\KlineReviewLauncher.manifest"
$icon = Join-Path $root "app_icon.ico"
$outputName = -join @(
    [char]0x540c, [char]0x82b1, [char]0x987a,
    "K",
    [char]0x7ebf, [char]0x590d, [char]0x76d8, [char]0x52a9, [char]0x624b,
    ".exe"
)
$output = Join-Path $root $outputName
$temporaryOutput = Join-Path $root "KlineReviewLauncher.exe"

$compilerCandidates = @(
    "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe",
    "$env:WINDIR\Microsoft.NET\Framework\v4.0.30319\csc.exe"
)
$compiler = $compilerCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $compiler) {
    throw "The .NET Framework C# compiler was not found."
}

foreach ($path in @($source, $manifest, $icon)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required launcher build input is missing: $path"
    }
}

Push-Location $root
try {
& $compiler /nologo /target:winexe /optimize+ /platform:anycpu `
    /reference:System.dll `
    /reference:System.Core.dll `
    /reference:System.Drawing.dll `
    /reference:System.Windows.Forms.dll `
    /win32icon:app_icon.ico `
    /win32manifest:launcher\KlineReviewLauncher.manifest `
    /out:KlineReviewLauncher.exe `
    launcher\KlineReviewLauncher.cs
if ($LASTEXITCODE -ne 0) {
    throw "Launcher compilation failed with exit code $LASTEXITCODE."
}
} finally {
    Pop-Location
}

[System.IO.File]::Copy($temporaryOutput, $output, $true)
[System.IO.File]::Delete($temporaryOutput)

$file = Get-Item -LiteralPath $output
$hash = Get-FileHash -LiteralPath $output -Algorithm SHA256
[PSCustomObject]@{
    Name = $file.Name
    Length = $file.Length
    SHA256 = $hash.Hash
}
