param(
    [ValidateRange(1024, 65535)][int]$Port = 17654,
    [string]$DiagnosticMesh
)

$ErrorActionPreference = "Stop"
$workspace = Split-Path -Parent $PSScriptRoot
$arguments = @((Join-Path $PSScriptRoot "Nav-Service.py"), "--port", "$Port")
if ($DiagnosticMesh) {
    $mesh = (Resolve-Path -LiteralPath $DiagnosticMesh).Path
    $arguments += @("--diagnostic-mesh", $mesh)
}
Write-Host "TrentNav binds only to 127.0.0.1:$Port. Ctrl+C stops the service. Live routes remain blocked."
& python @arguments
if ($LASTEXITCODE -ne 0) { throw "Local service exited with code $LASTEXITCODE" }
