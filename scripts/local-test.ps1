[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("setup", "start", "stop", "restart", "status", "smoke", "upgrade", "reset", "credentials", "verify")]
    [string]$Command = "status",

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $repoRoot ".local-test\venv\Scripts\python.exe"
$python = if (Test-Path -LiteralPath $venvPython) {
    $venvPython
} else {
    (Get-Command python -ErrorAction Stop).Source
}

& $python (Join-Path $PSScriptRoot "local_test.py") $Command @RemainingArgs
exit $LASTEXITCODE
