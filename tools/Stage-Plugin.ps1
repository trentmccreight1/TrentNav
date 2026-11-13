$ErrorActionPreference = "Stop"
$workspace = Split-Path -Parent $PSScriptRoot
$config = Get-Content -LiteralPath (Join-Path $workspace "config.lua") -Raw
if ($config -notmatch '(?m)^\s*dry_run\s*=\s*true\s*,') {
    throw "This diagnostic release must be packaged with dry_run = true"
}
$bundleRoot = Join-Path $workspace ".local/plugin-bundles"
New-Item -ItemType Directory -Path $bundleRoot -Force | Out-Null
$name = "TrentNav-" + (Get-Date -Format "yyyyMMdd-HHmmss") + "-" + [guid]::NewGuid().ToString("N").Substring(0, 8)
$destination = Join-Path $bundleRoot $name
$plugin = Join-Path $destination "TrentNav"
New-Item -ItemType Directory -Path (Join-Path $plugin "trentnav") -Force | Out-Null
$files = @("header.lua", "main.lua", "config.lua", "trentnav/engine.lua", "trentnav/safety.lua", "trentnav/navigation.lua")
$manifest = @()
foreach ($relative in $files) {
    $source = Join-Path $workspace $relative
    $target = Join-Path $plugin $relative
    Copy-Item -LiteralPath $source -Destination $target
    $manifest += [ordered]@{ path = $relative; sha256 = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() }
}
Copy-Item -LiteralPath (Join-Path $workspace "TESTING.md") -Destination (Join-Path $destination "TESTING.md")
[ordered]@{ version = "0.2.0"; build = 70888; dry_run = $true; navigation_ready = $false; files = $manifest } |
    ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $destination "manifest.json") -Encoding UTF8
$archive = Join-Path $bundleRoot ($name + ".zip")
Compress-Archive -LiteralPath $plugin, (Join-Path $destination "TESTING.md"), (Join-Path $destination "manifest.json") -DestinationPath $archive
Write-Output "Plugin folder: $plugin"
Write-Output "Archive: $archive"
Write-Output "Diagnostic package only; no game assets, .api stubs or third-party navigation code included."
