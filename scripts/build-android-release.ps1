[CmdletBinding()]
param(
    [string]$OutputDirectory
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

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

$RepoRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot)).TrimEnd("\")
$SigningRoot = Join-Path $RepoRoot ".release-signing"
$CredentialsPath = Join-Path $SigningRoot "credentials.json"
$VersionFile = Join-Path $RepoRoot "gateway\nexus_gateway\__init__.py"
$VersionMatch = Select-String -LiteralPath $VersionFile -Pattern '__version__\s*=\s*"([^"]+)"'
if (-not $VersionMatch) { throw "Unable to read the Nexus release version." }
$Version = $VersionMatch.Matches[0].Groups[1].Value
$DefaultOutput = Join-Path $RepoRoot ("成品\v" + $Version)
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) { $OutputDirectory = $DefaultOutput }
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
if (-not $OutputDirectory.StartsWith($RepoRoot + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Release output must stay inside the Nexus repository."
}
if (-not (Test-Path -LiteralPath $CredentialsPath -PathType Leaf)) {
    throw "Release signing credentials are not initialized in .release-signing."
}

$credentials = Get-Content -LiteralPath $CredentialsPath -Raw -Encoding UTF8 | ConvertFrom-Json
$requiredFields = @("store_file", "store_password", "key_alias", "key_password")
foreach ($field in $requiredFields) {
    if ([string]::IsNullOrWhiteSpace([string]$credentials.$field)) {
        throw "Release signing credentials are incomplete."
    }
}
$storeFile = [System.IO.Path]::GetFullPath((Join-Path $SigningRoot ([string]$credentials.store_file)))
if (-not $storeFile.StartsWith($SigningRoot + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Signing store path escapes .release-signing."
}
if (-not (Test-Path -LiteralPath $storeFile -PathType Leaf)) { throw "Release signing store is missing." }

$env:NEXUS_RELEASE_STORE_FILE = $storeFile
$env:NEXUS_RELEASE_STORE_PASSWORD = [string]$credentials.store_password
$env:NEXUS_RELEASE_KEY_ALIAS = [string]$credentials.key_alias
$env:NEXUS_RELEASE_KEY_PASSWORD = [string]$credentials.key_password
if ([string]::IsNullOrWhiteSpace($env:ANDROID_SDK_ROOT)) {
    $env:ANDROID_SDK_ROOT = Join-Path $env:LOCALAPPDATA "Android\Sdk"
}

try {
    Push-Location (Join-Path $RepoRoot "android")
    try {
        & .\gradlew.bat testDebugUnitTest lintDebug assembleRelease bundleRelease --no-daemon --max-workers=1 "-Dkotlin.compiler.execution.strategy=in-process"
        if ($LASTEXITCODE -ne 0) { throw "Android release build failed." }
    } finally {
        Pop-Location
    }

    $pythonCommand = Find-NexusPython (Join-Path $RepoRoot ".local-test\venv\Scripts\python.exe")
    $releaseArgs = @(
        (Join-Path $RepoRoot "scripts\build_release.py"),
        "--output", $OutputDirectory,
        "--apk", (Join-Path $RepoRoot "android\app\build\outputs\apk\release\app-release.apk"),
        "--aab", (Join-Path $RepoRoot "android\app\build\outputs\bundle\release\app-release.aab"),
        "--require-android",
        "--verify-signatures"
    )
    if (-not [string]::IsNullOrWhiteSpace([string]$credentials.certificate_sha256)) {
        $releaseArgs += @("--certificate-sha256", [string]$credentials.certificate_sha256)
    }
    & $pythonCommand @releaseArgs
    if ($LASTEXITCODE -ne 0) { throw "Release artifact packaging failed." }
} finally {
    Remove-Item Env:NEXUS_RELEASE_STORE_FILE -ErrorAction SilentlyContinue
    Remove-Item Env:NEXUS_RELEASE_STORE_PASSWORD -ErrorAction SilentlyContinue
    Remove-Item Env:NEXUS_RELEASE_KEY_ALIAS -ErrorAction SilentlyContinue
    Remove-Item Env:NEXUS_RELEASE_KEY_PASSWORD -ErrorAction SilentlyContinue
}
