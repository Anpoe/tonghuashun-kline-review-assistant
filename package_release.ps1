$ErrorActionPreference = "Stop"
Write-Warning "package_release.ps1 已停用，请使用 build_release.ps1。"
& (Join-Path $PSScriptRoot "build_release.ps1")
exit $LASTEXITCODE
