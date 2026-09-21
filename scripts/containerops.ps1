param(
    [Parameter(Position = 0, Mandatory = $true)]
    [ValidateSet('setup','build','test','start','status','demo','logs','inspect','verify','backup','restore-test','release','rollback','stop','report','scan','sbom','cache-builds','tls','prove')]
    [string]$Command,
    [ValidateSet('1.0.0','2.0.0')]
    [string]$Version,
    [switch]$InjectSmokeFailure,
    [switch]$Offline
)
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$entrypoint = Join-Path $PSScriptRoot 'ops.py'
$arguments = @($entrypoint, $Command)
if ($Version) { $arguments += @('--version', $Version) }
if ($InjectSmokeFailure) { $arguments += '--inject-smoke-failure' }
if ($Offline) { $arguments += '--offline' }
Push-Location -LiteralPath $project
try {
    & python @arguments
    $nativeExitCode = $LASTEXITCODE
    if ($nativeExitCode -ne 0) { throw "ContainerOps falhou com código $nativeExitCode" }
} finally {
    Pop-Location
}
