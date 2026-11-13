$ErrorActionPreference = "Stop"
$workspace = Split-Path -Parent $PSScriptRoot
$destination = Join-Path $workspace ".local/WoW-tact-keys.txt"
$revision = "1f8ba0ff780db15b1f749a4e36b8c4e2d73f44a4"
$expectedHash = "c5354516e1bb787c9fc6982c8e82ca7d8a9ce6355484476a6e261714a61707b9"
New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
if (!(Test-Path -LiteralPath $destination)) {
    Invoke-WebRequest -UseBasicParsing -Uri "https://raw.githubusercontent.com/wowdev/TACTKeys/$revision/WoW.txt" -OutFile $destination
}
$hash = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant()
if ($hash -ne $expectedHash) { throw "Asset-key list hash mismatch; refusing to use it" }
Write-Host "Verified local asset-key list: $destination"
