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

function Test-NexusPythonPath([string]$Candidate) {
    if ([string]::IsNullOrWhiteSpace($Candidate)) { return $false }
    try { $full = [System.IO.Path]::GetFullPath($Candidate) } catch { return $false }
    if (-not (Test-Path -LiteralPath $full -PathType Leaf)) { return $false }
    $parts = @($full -split '[\\/]')
    if (@($parts | Where-Object { $_ -ieq "hermes" }).Count -gt 0) { return $false }
    return $true
}

function Find-NexusPython([string]$ManagedCandidate) {
    $candidates = New-Object System.Collections.Generic.List[string]
    if (-not [string]::IsNullOrWhiteSpace($ManagedCandidate)) { $candidates.Add($ManagedCandidate) }
    if (-not [string]::IsNullOrWhiteSpace($env:NEXUS_PYTHON)) { $candidates.Add($env:NEXUS_PYTHON) }
    if (-not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        Get-ChildItem -LiteralPath (Join-Path $env:LOCALAPPDATA "Programs\Python") -Filter python.exe -File -Recurse -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending |
            ForEach-Object { $candidates.Add($_.FullName) }
    }
    foreach ($name in @("python", "python3")) {
        Get-Command $name -All -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandType -eq "Application" } |
            ForEach-Object { $candidates.Add($_.Source) }
    }
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($null -ne $launcher) {
        $launcherOutput = & $launcher.Source -3 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $null -ne $launcherOutput) {
            $candidates.Add([string]($launcherOutput | Select-Object -First 1))
        }
    }
    $seen = @{}
    foreach ($candidate in $candidates) {
        try { $full = [System.IO.Path]::GetFullPath([string]$candidate) } catch { continue }
        if ($seen.ContainsKey($full)) { continue }
        $seen[$full] = $true
        if (Test-NexusPythonPath $full) { return $full }
    }
    throw "No independent Python installation was found. Set NEXUS_PYTHON to a Python executable outside Hermes."
}

$python = Find-NexusPython ""
& $python (Join-Path $PSScriptRoot "local_test.py") $Command @RemainingArgs
exit $LASTEXITCODE
