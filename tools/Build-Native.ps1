param([switch]$FetchDependencies)

$ErrorActionPreference = "Stop"
$workspace = Split-Path -Parent $PSScriptRoot
$source = Join-Path $workspace ".local/trinitycore"
$revision = "3da55c1d62db78bb89c98e870645fff17b3ec80c"

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed with exit code $LASTEXITCODE" }
}

if (!(Test-Path -LiteralPath (Join-Path $source ".git"))) {
    if (!$FetchDependencies) { throw "Pinned source is missing. Run with -FetchDependencies to download it." }
    New-Item -ItemType Directory -Path (Join-Path $workspace ".local") -Force | Out-Null
    Invoke-Checked "git" @("clone", "--depth", "1", "--filter=blob:none", "--no-checkout", "--single-branch", "--branch", "forever", "https://github.com/advocaite/TrinityCore.git", $source)
    Invoke-Checked "git" @("-C", $source, "fetch", "--depth", "1", "origin", $revision)
    Invoke-Checked "git" @("-C", $source, "sparse-checkout", "init", "--cone")
    Invoke-Checked "git" @("-C", $source, "sparse-checkout", "set", "dep")
    Invoke-Checked "git" @("-C", $source, "checkout", "--detach", $revision)
}

$actualRevision = (& git -C $source rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $actualRevision -ne $revision) { throw "Extraction dependency revision mismatch" }
Invoke-Checked "git" @("-C", $source, "diff", "--exit-code", "HEAD", "--", "dep/CascLib", "dep/zlib", "dep/recastnavigation")
$vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio/Installer/vswhere.exe"
if (!(Test-Path -LiteralPath $vswhere)) { throw "Visual Studio C++ discovery tool is missing" }
$visualStudio = & $vswhere -latest -products "*" -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (!$visualStudio) { throw "Visual Studio C++ tools are required" }
$cmake = Join-Path $visualStudio "Common7/IDE/CommonExtensions/Microsoft/CMake/CMake/bin/cmake.exe"
if (!(Test-Path -LiteralPath $cmake)) { throw "Visual Studio CMake is required" }
$build = Join-Path $workspace ".local/native-build"
Invoke-Checked $cmake @("-S", (Join-Path $workspace "native"), "-B", $build, "-G", "Visual Studio 17 2022", "-A", "x64", "-DTRINITY_SOURCE=$source")
Invoke-Checked $cmake @("--build", $build, "--config", "Release", "--target", "trentnav_client_probe", "trentnav_mesh_smoke", "trentnav_mesh_query", "--parallel", "6")
