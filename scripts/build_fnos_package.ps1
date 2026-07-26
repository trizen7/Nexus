[CmdletBinding()]
param(
    [string]$OutputDirectory = "dist",
    [string]$FnpackPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$PackageSource = Join-Path $RepositoryRoot "fnos/nexus-gateway"
$StagingRoot = Join-Path $RepositoryRoot ".local-test/fnos-build"
$StagingPackage = Join-Path $StagingRoot "nexus-gateway"
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$OfficialFnpackVersion = "1.2.3"
$OfficialWindowsSha256 = "d7af4bd716b009c58f5bcd931615f39db121e7d4b75dc759e575c4fb2879b6ee"
$OfficialLinuxAmd64Sha256 = "54b97fa7b70968c4d05c79840f5daeff508957d0bb2062fdb0376d00d9615c93"
$RunningOnWindows = $env:OS -eq "Windows_NT"

function Resolve-RepositoryPath([string]$Value, [string]$Label) {
    $Candidate = if ([System.IO.Path]::IsPathRooted($Value)) { $Value } else { Join-Path $RepositoryRoot $Value }
    $Resolved = [System.IO.Path]::GetFullPath($Candidate)
    $Prefix = $RepositoryRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
    if (-not $Resolved.StartsWith($Prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label must stay inside the Nexus repository"
    }
    if ($Resolved -eq $RepositoryRoot) {
        throw "$Label cannot be the repository root"
    }
    return $Resolved
}

function Remove-ContainedTree([string]$Path, [string]$AllowedRoot) {
    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    $ResolvedPath = [System.IO.Path]::GetFullPath($Path)
    $ResolvedAllowed = [System.IO.Path]::GetFullPath($AllowedRoot).TrimEnd([System.IO.Path]::DirectorySeparatorChar)
    $Prefix = $ResolvedAllowed + [System.IO.Path]::DirectorySeparatorChar
    if (-not $ResolvedPath.StartsWith($Prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "refusing to remove a path outside the fnOS build workspace: $ResolvedPath"
    }
    Remove-Item -LiteralPath $ResolvedPath -Recurse -Force
}

function Read-ManifestValue([string]$Manifest, [string]$Name) {
    $Match = [regex]::Match($Manifest, "(?m)^" + [regex]::Escape($Name) + "\s*=\s*(.+?)\s*$")
    if (-not $Match.Success) {
        throw "manifest is missing $Name"
    }
    return $Match.Groups[1].Value.Trim()
}

function Normalize-StagingText([string]$Root) {
    $TextNames = @("LICENSE", "manifest", "config", "privilege", "resource", "install", "upgrade", "uninstall", "main", "config_init", "config_callback", "install_init", "install_callback", "upgrade_init", "upgrade_callback", "uninstall_init", "uninstall_callback")
    $TextExtensions = @(".py", ".sh", ".json", ".yaml", ".yml", ".txt")
    Get-ChildItem -LiteralPath $Root -Recurse -File | ForEach-Object {
        if (($TextNames -contains $_.Name) -or ($TextExtensions -contains $_.Extension.ToLowerInvariant())) {
            $Text = [System.IO.File]::ReadAllText($_.FullName)
            $Text = $Text.Replace("`r`n", "`n").Replace("`r", "`n")
            [System.IO.File]::WriteAllText($_.FullName, $Text, $Utf8NoBom)
        }
    }
}

function Resolve-Fnpack([string]$RequestedPath) {
    if ($RequestedPath) {
        $Resolved = [System.IO.Path]::GetFullPath($RequestedPath)
        if (-not (Test-Path -LiteralPath $Resolved -PathType Leaf)) {
            throw "fnpack was not found: $Resolved"
        }
        return $Resolved
    }
    if (-not $RunningOnWindows) {
        throw "-FnpackPath is required outside Windows"
    }
    $ToolDirectory = Join-Path $RepositoryRoot ".local-test/tools"
    $Resolved = Join-Path $ToolDirectory "fnpack-$OfficialFnpackVersion-windows-amd64.exe"
    if (-not (Test-Path -LiteralPath $Resolved -PathType Leaf)) {
        New-Item -ItemType Directory -Path $ToolDirectory -Force | Out-Null
        Invoke-WebRequest -Uri "https://static2.fnnas.com/fnpack/fnpack-$OfficialFnpackVersion-windows-amd64" -OutFile $Resolved
    }
    return $Resolved
}

function Update-ChecksumManifest([string]$Directory, [string]$ArtifactName) {
    $ManifestPath = Join-Path $Directory "SHA256SUMS.txt"
    $Names = @{}
    if (Test-Path -LiteralPath $ManifestPath -PathType Leaf) {
        foreach ($RawLine in [System.IO.File]::ReadAllLines($ManifestPath, [System.Text.Encoding]::UTF8)) {
            if ([string]::IsNullOrWhiteSpace($RawLine)) { continue }
            $Match = [regex]::Match($RawLine, '^([0-9A-Fa-f]{64})  ([^\x2f\x5c]+)$')
            if (-not $Match.Success) {
                throw "invalid SHA256SUMS.txt line"
            }
            $Name = $Match.Groups[2].Value
            if ($Name -eq "SHA256SUMS.txt" -or $Name -ne [System.IO.Path]::GetFileName($Name)) {
                throw "unsafe SHA256SUMS.txt artifact name"
            }
            if ($Names.ContainsKey($Name)) {
                throw "duplicate SHA256SUMS.txt artifact: $Name"
            }
            $Names[$Name] = $true
        }
    }
    $Names[$ArtifactName] = $true

    $Lines = @()
    foreach ($Name in @($Names.Keys | Sort-Object)) {
        $ArtifactPath = Join-Path $Directory $Name
        if (-not (Test-Path -LiteralPath $ArtifactPath -PathType Leaf)) {
            throw "SHA256SUMS.txt references a missing artifact: $Name"
        }
        $ArtifactDigest = (Get-FileHash -LiteralPath $ArtifactPath -Algorithm SHA256).Hash.ToLowerInvariant()
        $Lines += "$ArtifactDigest  $Name"
    }
    [System.IO.File]::WriteAllText($ManifestPath, (($Lines -join "`n") + "`n"), $Utf8NoBom)
    return $ManifestPath
}

function Test-Fnpack([string]$Executable) {
    $Name = [System.IO.Path]::GetFileName($Executable)
    $ExpectedHash = switch ($Name) {
        "fnpack-$OfficialFnpackVersion-windows-amd64.exe" { $OfficialWindowsSha256 }
        "fnpack-$OfficialFnpackVersion-windows-amd64" { $OfficialWindowsSha256 }
        "fnpack-$OfficialFnpackVersion-linux-amd64" { $OfficialLinuxAmd64Sha256 }
        default { "" }
    }
    if ($ExpectedHash) {
        $ActualHash = (Get-FileHash -LiteralPath $Executable -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($ActualHash -ne $ExpectedHash) {
            throw "fnpack SHA-256 verification failed"
        }
    }
    $Help = (& $Executable --help 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0 -or $Help -notmatch "Version\s+$([regex]::Escape($OfficialFnpackVersion))") {
        throw "fnpack $OfficialFnpackVersion is required"
    }
}

$ResolvedOutput = Resolve-RepositoryPath $OutputDirectory "output directory"
$Fnpack = Resolve-Fnpack $FnpackPath
Test-Fnpack $Fnpack

$GatewayInit = [System.IO.File]::ReadAllText((Join-Path $RepositoryRoot "gateway/nexus_gateway/__init__.py"))
$GatewayMatch = [regex]::Match($GatewayInit, '__version__\s*=\s*"([^"]+)"')
if (-not $GatewayMatch.Success) {
    throw "could not read the Gateway version"
}
$GatewayVersion = $GatewayMatch.Groups[1].Value
$Manifest = [System.IO.File]::ReadAllText((Join-Path $PackageSource "manifest"))
$PackageVersion = Read-ManifestValue $Manifest "version"
if ($PackageVersion -notmatch ("^" + [regex]::Escape($GatewayVersion) + "-fnos[1-9][0-9]*$")) {
    throw "fnOS package version $PackageVersion must be based on Gateway $GatewayVersion"
}
$Compose = [System.IO.File]::ReadAllText((Join-Path $PackageSource "app/docker/docker-compose.yaml"))
$ExpectedImage = "ghcr.io/trizen7/nexus-gateway:$GatewayVersion"
if ($Compose -notmatch ("(?m)^\s*image:\s*" + [regex]::Escape($ExpectedImage) + "\s*$")) {
    throw "fnOS Compose image must be $ExpectedImage"
}

New-Item -ItemType Directory -Path $StagingRoot -Force | Out-Null
Remove-ContainedTree $StagingPackage $StagingRoot
Copy-Item -LiteralPath $PackageSource -Destination $StagingPackage -Recurse
Normalize-StagingText $StagingPackage

New-Item -ItemType Directory -Path $ResolvedOutput -Force | Out-Null
$OutputName = "Nexus-fnOS-$PackageVersion.fpk"
$OutputPath = Join-Path $ResolvedOutput $OutputName
$LegacyHashPath = "$OutputPath.sha256"
Remove-Item -LiteralPath $OutputPath -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $LegacyHashPath -Force -ErrorAction SilentlyContinue

Push-Location $StagingRoot
try {
    & $Fnpack build --directory $StagingPackage
    if ($LASTEXITCODE -ne 0) {
        throw "fnpack build failed with exit code $LASTEXITCODE"
    }
    $Generated = Join-Path $StagingRoot "nexus-gateway.fpk"
    if (-not (Test-Path -LiteralPath $Generated -PathType Leaf)) {
        throw "fnpack did not produce nexus-gateway.fpk"
    }
    Copy-Item -LiteralPath $Generated -Destination $OutputPath
}
finally {
    Pop-Location
    Remove-ContainedTree $StagingPackage $StagingRoot
    Remove-Item -LiteralPath (Join-Path $StagingRoot "nexus-gateway.fpk") -Force -ErrorAction SilentlyContinue
}

$Digest = (Get-FileHash -LiteralPath $OutputPath -Algorithm SHA256).Hash.ToLowerInvariant()
$ChecksumManifest = Update-ChecksumManifest $ResolvedOutput $OutputName

Write-Host "Built $OutputPath"
Write-Host "Updated $ChecksumManifest"
Write-Host "SHA-256 $Digest"
