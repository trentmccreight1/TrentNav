param(
    [string]$ClientPath = "C:\Program Files (x86)\World of Warcraft\_forever_",
    [string]$WdtName = "world\maps\azeroth\azeroth.wdt",
    [uint32]$WdtFileDataId = 0,
    [ValidateRange(0, 63)][int]$TileX = 32,
    [ValidateRange(0, 63)][int]$TileY = 48,
    [string]$KeysFile,
    [switch]$MetadataOnly
)

$ErrorActionPreference = "Stop"
$workspace = Split-Path -Parent $PSScriptRoot
$client = (Resolve-Path -LiteralPath $ClientPath).Path
$executable = Join-Path $client "Wow.exe"
if (!(Test-Path -LiteralPath $executable -PathType Leaf)) { throw "Wow.exe is missing from ClientPath" }
$version = (Get-Item -LiteralPath $executable).VersionInfo.FileVersion
if ($version -ne "1.60.1.70888") { throw "Client build changed ($version). Revalidate extraction compatibility before continuing." }
$root = Split-Path -Parent $client
$probe = Join-Path $workspace ".local/native-build/Release/trentnav_client_probe.exe"
if (!(Test-Path -LiteralPath $probe)) { throw "Build the owned probe with tools/Build-Native.ps1 first" }
$receipts = Join-Path $workspace ".local/client-probes"
New-Item -ItemType Directory -Path $receipts -Force | Out-Null
$output = Join-Path $receipts ((Get-Date -Format "yyyyMMdd-HHmmss") + "-" + [guid]::NewGuid().ToString("N"))
$probeArguments = @($root, $output)
if ($KeysFile) {
    $KeysFile = (Resolve-Path -LiteralPath $KeysFile).Path
    $probeArguments += @("--keys", $KeysFile)
}
if (!$MetadataOnly) {
    if ($WdtFileDataId -eq 0 -and $WdtName -eq "world\maps\azeroth\azeroth.wdt") { $WdtFileDataId = 775971 }
    $wdtIdentity = $(if ($WdtFileDataId -gt 0) { "id:$WdtFileDataId" } else { $WdtName })
    $probeArguments += @($wdtIdentity, [string]$TileX, [string]$TileY)
}
$started = [DateTimeOffset]::UtcNow.ToString("o")
$previousErrorPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    $messages = @(& $probe @probeArguments 2>&1 | ForEach-Object { $_.ToString() })
    $exitCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $previousErrorPreference
}
if (!(Test-Path -LiteralPath $output)) { New-Item -ItemType Directory -Path $output | Out-Null }
$messages | Set-Content -LiteralPath (Join-Path $output "probe.log") -Encoding UTF8
$files = @(Get-ChildItem -LiteralPath $output -File -Recurse | Where-Object { $_.Extension -in @(".db2", ".adt", ".wdt", ".bin") -or $_.Name -eq "collision-assets.json" } | ForEach-Object {
    @{ name = $_.FullName.Substring($output.Length + 1).Replace('\', '/'); size_bytes = $_.Length; sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
})
$dependencies = $null
$dependencyManifest = Join-Path $output "collision-assets.json"
if (Test-Path -LiteralPath $dependencyManifest) { $dependencies = Get-Content -LiteralPath $dependencyManifest -Raw | ConvertFrom-Json }
$receipt = [ordered]@{
    schema_version = 1
    started_utc = $started
    finished_utc = [DateTimeOffset]::UtcNow.ToString("o")
    client_version = $version
    product = "wowf"
    dependency_revision = "3da55c1d62db78bb89c98e870645fff17b3ec80c"
    probe_sha256 = (Get-FileHash -LiteralPath $probe -Algorithm SHA256).Hash.ToLowerInvariant()
    offline_storage_required = $true
    asset_keys_sha256 = $(if ($KeysFile) { (Get-FileHash -LiteralPath $KeysFile -Algorithm SHA256).Hash.ToLowerInvariant() } else { $null })
    metadata_only = [bool]$MetadataOnly
    wdt_name = $(if ($MetadataOnly) { $null } else { $WdtName })
    wdt_file_data_id = $(if ($MetadataOnly -or $WdtFileDataId -eq 0) { $null } else { $WdtFileDataId })
    tile_x = $(if ($MetadataOnly) { $null } else { $TileX })
    tile_y = $(if ($MetadataOnly) { $null } else { $TileY })
    exit_code = $exitCode
    status = $(if ($exitCode -ne 0) { "failed" } elseif (!$MetadataOnly -and (!$dependencies -or !$dependencies.dependencies_complete)) { "asset_probe_incomplete" } else { "asset_probe_complete" })
    collision_dependencies_complete = [bool]($dependencies -and $dependencies.dependencies_complete)
    collision_complete = $false
    navigation_ready = $false
    files = $files
}
$receipt | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $output "receipt.json") -Encoding UTF8
$messages | ForEach-Object { Write-Host $_ }
Write-Host "Receipt: $output"
if ($exitCode -ne 0) { throw "Offline client probe failed; inspect the retained receipt and log" }
