[CmdletBinding()]
param(
    [string]$OutputDirectory = "dist",
    [string]$FnpackPath = "",
    [string]$PythonPath = "",
    [Parameter(Mandatory = $true)]
    [ValidateSet("amd64", "arm64")]
    [string]$Platform,
    [Parameter(Mandatory = $true)]
    [string]$RuntimeDirectoryPath
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
$RuntimePlatformName = "runtime.platform"
$RuntimeChecksumName = "runtime.sha256"
$RuntimeExecutableName = "nexus-gateway/nexus-gateway"

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

function Resolve-InputDirectory([string]$Value, [string]$Label) {
    $Candidate = if ([System.IO.Path]::IsPathRooted($Value)) { $Value } else { Join-Path $RepositoryRoot $Value }
    $Resolved = [System.IO.Path]::GetFullPath($Candidate)
    if (-not (Test-Path -LiteralPath $Resolved -PathType Container)) {
        throw "$Label was not found: $Resolved"
    }
    return $Resolved
}

function Get-ContainedRelativePath([string]$Root, [string]$Target) {
    $ResolvedRoot = [System.IO.Path]::GetFullPath($Root).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    $ResolvedTarget = [System.IO.Path]::GetFullPath($Target)
    $Prefix = $ResolvedRoot + [System.IO.Path]::DirectorySeparatorChar
    $Comparison = if ($RunningOnWindows) {
        [System.StringComparison]::OrdinalIgnoreCase
    }
    else {
        [System.StringComparison]::Ordinal
    }
    if (-not $ResolvedTarget.StartsWith($Prefix, $Comparison)) {
        throw "Gateway runtime file escapes the runtime directory: $ResolvedTarget"
    }
    return $ResolvedTarget.Substring($Prefix.Length).Replace('\', '/')
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
    $TextExtensions = @(".py", ".sh", ".json", ".yaml", ".yml", ".txt", ".sha256", ".platform")
    Get-ChildItem -LiteralPath $Root -Recurse -File | ForEach-Object {
        if (($TextNames -contains $_.Name) -or ($TextExtensions -contains $_.Extension.ToLowerInvariant())) {
            $Text = [System.IO.File]::ReadAllText($_.FullName)
            $Text = $Text.Replace("`r`n", "`n").Replace("`r", "`n")
            [System.IO.File]::WriteAllText($_.FullName, $Text, $Utf8NoBom)
        }
    }
}

function Test-NexusPythonPath([string]$Candidate) {
    if ([string]::IsNullOrWhiteSpace($Candidate)) { return $false }
    try { $Full = [System.IO.Path]::GetFullPath($Candidate) } catch { return $false }
    if (-not (Test-Path -LiteralPath $Full -PathType Leaf)) { return $false }
    $Parts = @($Full -split '[\\/]')
    if (@($Parts | Where-Object { $_ -ieq "hermes" }).Count -gt 0) { return $false }
    return $true
}

function Resolve-NexusPython([string]$RequestedPath) {
    $Candidates = New-Object System.Collections.Generic.List[string]
    if (-not [string]::IsNullOrWhiteSpace($RequestedPath)) {
        $Candidates.Add($RequestedPath)
    }
    if ($RunningOnWindows) {
        $Candidates.Add((Join-Path $RepositoryRoot ".local-test/venv/Scripts/python.exe"))
    }
    else {
        if (-not [string]::IsNullOrWhiteSpace($env:NEXUS_PYTHON)) {
            $Candidates.Add($env:NEXUS_PYTHON)
        }
        foreach ($Name in @("python3", "python")) {
            $Command = Get-Command $Name -ErrorAction SilentlyContinue
            if ($null -ne $Command -and $Command.CommandType -eq "Application") {
                $Candidates.Add($Command.Source)
            }
        }
    }
    $Seen = @{}
    foreach ($Candidate in $Candidates) {
        try { $Full = [System.IO.Path]::GetFullPath([string]$Candidate) } catch { continue }
        if ($Seen.ContainsKey($Full)) { continue }
        $Seen[$Full] = $true
        if (Test-NexusPythonPath $Full) { return $Full }
    }
    throw "A Nexus-owned Python runtime is required to normalize the fnOS package."
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
            continue
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

function Assert-NoLinks([string]$Root) {
    $Items = @((Get-Item -LiteralPath $Root -Force)) + @(Get-ChildItem -LiteralPath $Root -Recurse -Force)
    foreach ($Item in $Items) {
        $IsReparsePoint = (($Item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)
        $HasLinkType = $Item.PSObject.Properties.Name -contains "LinkType" -and -not [string]::IsNullOrEmpty([string]$Item.LinkType)
        if ($IsReparsePoint -or $HasLinkType) {
            throw "Gateway runtime must not contain links: $($Item.FullName)"
        }
    }
}

function Assert-RuntimeLayout([string]$RuntimeRoot, [hashtable]$PlatformMetadata) {
    Assert-NoLinks $RuntimeRoot
    $TopLevel = @(Get-ChildItem -LiteralPath $RuntimeRoot -Force | ForEach-Object { $_.Name } | Sort-Object)
    $ExpectedTopLevel = @("ca-certificates.crt", "nexus-gateway")
    if (($TopLevel -join "`n") -ne ($ExpectedTopLevel -join "`n")) {
        throw "Gateway runtime must contain only ca-certificates.crt and nexus-gateway"
    }
    $GatewayDirectory = Join-Path $RuntimeRoot "nexus-gateway"
    $Executable = Join-Path $RuntimeRoot ($RuntimeExecutableName.Replace('/', [System.IO.Path]::DirectorySeparatorChar))
    $CaBundle = Join-Path $RuntimeRoot "ca-certificates.crt"
    if (-not (Test-Path -LiteralPath $GatewayDirectory -PathType Container)) {
        throw "Gateway runtime directory is missing"
    }
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
        throw "Gateway runtime executable is missing"
    }
    if (-not (Test-Path -LiteralPath $CaBundle -PathType Leaf)) {
        throw "Gateway runtime CA bundle is missing"
    }
    $Bytes = [System.IO.File]::ReadAllBytes($Executable)
    if ($Bytes.Length -lt 20 -or $Bytes[0] -ne 0x7f -or $Bytes[1] -ne 0x45 -or $Bytes[2] -ne 0x4c -or $Bytes[3] -ne 0x46) {
        throw "Gateway runtime executable is not ELF"
    }
    if ($Bytes[4] -ne 2 -or $Bytes[5] -ne 1) {
        throw "Gateway runtime executable must be 64-bit little-endian ELF"
    }
    $Machine = [int]$Bytes[18] + (256 * [int]$Bytes[19])
    if ($Machine -ne $PlatformMetadata.ElfMachine) {
        throw "Gateway runtime executable architecture does not match $Platform"
    }
}

function Write-RuntimeChecksumManifest([string]$RuntimeRoot) {
    $Files = @(Get-ChildItem -LiteralPath $RuntimeRoot -Recurse -File | Where-Object { $_.Name -ne $RuntimeChecksumName })
    $RelativePaths = [string[]]@($Files | ForEach-Object {
        $Relative = Get-ContainedRelativePath $RuntimeRoot $_.FullName
        if ($Relative.StartsWith('/') -or $Relative -match '(^|/)\.\.(/|$)' -or $Relative.Contains("`n") -or $Relative.Contains("`r")) {
            throw "unsafe Gateway runtime path: $Relative"
        }
        $Relative
    })
    [Array]::Sort($RelativePaths, [System.StringComparer]::Ordinal)
    $Lines = @()
    foreach ($Relative in $RelativePaths) {
        $NativeRelative = $Relative.Replace('/', [System.IO.Path]::DirectorySeparatorChar)
        $Target = Join-Path $RuntimeRoot $NativeRelative
        $Digest = (Get-FileHash -LiteralPath $Target -Algorithm SHA256).Hash.ToLowerInvariant()
        $Lines += "$Digest  $Relative"
    }
    if ($Lines.Count -eq 0) {
        throw "Gateway runtime contains no files"
    }
    [System.IO.File]::WriteAllText(
        (Join-Path $RuntimeRoot $RuntimeChecksumName),
        (($Lines -join "`n") + "`n"),
        $Utf8NoBom
    )
}

$ResolvedOutput = Resolve-RepositoryPath $OutputDirectory "output directory"
$ResolvedRuntime = Resolve-InputDirectory $RuntimeDirectoryPath "Gateway runtime directory"
$Fnpack = Resolve-Fnpack $FnpackPath
Test-Fnpack $Fnpack
$Python = Resolve-NexusPython $PythonPath

$PlatformMetadata = switch ($Platform) {
    "amd64" { @{ Manifest = "x86"; Runtime = "linux/amd64"; ElfMachine = 62 } }
    "arm64" { @{ Manifest = "arm"; Runtime = "linux/arm64"; ElfMachine = 183 } }
    default { throw "unsupported fnOS package platform: $Platform" }
}
Assert-RuntimeLayout $ResolvedRuntime $PlatformMetadata

$GatewayInit = [System.IO.File]::ReadAllText((Join-Path $RepositoryRoot "gateway/nexus_gateway/__init__.py"))
$GatewayMatch = [regex]::Match($GatewayInit, '__version__\s*=\s*"([^"]+)"')
if (-not $GatewayMatch.Success) {
    throw "could not read the Gateway version"
}
$GatewayVersion = $GatewayMatch.Groups[1].Value
$Manifest = [System.IO.File]::ReadAllText((Join-Path $PackageSource "manifest"))
$PackageVersion = Read-ManifestValue $Manifest "version"
if ($PackageVersion -ne $GatewayVersion) {
    throw "fnOS package version $PackageVersion must equal Gateway $GatewayVersion"
}
if ((Read-ManifestValue $Manifest "platform") -ne "all") {
    throw "the fnOS source manifest must remain an architecture-neutral build template"
}
if (Test-Path -LiteralPath (Join-Path $PackageSource "app/runtime")) {
    throw "generated fnOS runtime files must not be committed to the package source"
}
if (Test-Path -LiteralPath (Join-Path $PackageSource "app/docker")) {
    throw "fnOS package source must not contain a container project"
}

New-Item -ItemType Directory -Path $StagingRoot -Force | Out-Null
Remove-ContainedTree $StagingPackage $StagingRoot
Copy-Item -LiteralPath $PackageSource -Destination $StagingPackage -Recurse
Normalize-StagingText $StagingPackage

$StagingManifestPath = Join-Path $StagingPackage "manifest"
$StagingManifest = [System.IO.File]::ReadAllText($StagingManifestPath)
$StagingManifest = [regex]::Replace(
    $StagingManifest,
    '(?m)^platform\s*=\s*all\s*$',
    "platform=$($PlatformMetadata.Manifest)"
)
if ((Read-ManifestValue $StagingManifest "platform") -ne $PlatformMetadata.Manifest) {
    throw "failed to set the fnOS package platform"
}
[System.IO.File]::WriteAllText($StagingManifestPath, $StagingManifest, $Utf8NoBom)

$StagingRuntime = Join-Path $StagingPackage "app/runtime"
New-Item -ItemType Directory -Path $StagingRuntime -Force | Out-Null
Get-ChildItem -LiteralPath $ResolvedRuntime -Force | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $StagingRuntime -Recurse
}
Assert-NoLinks $StagingRuntime
[System.IO.File]::WriteAllText(
    (Join-Path $StagingRuntime $RuntimePlatformName),
    "$($PlatformMetadata.Runtime)`n",
    $Utf8NoBom
)
if (-not $RunningOnWindows) {
    $StagingExecutable = Join-Path $StagingRuntime ($RuntimeExecutableName.Replace('/', [System.IO.Path]::DirectorySeparatorChar))
    & chmod 0755 $StagingExecutable
    if ($LASTEXITCODE -ne 0) {
        throw "failed to mark the Gateway runtime executable"
    }
}
Write-RuntimeChecksumManifest $StagingRuntime

New-Item -ItemType Directory -Path $ResolvedOutput -Force | Out-Null
$OutputName = "Nexus-fnOS-$PackageVersion-$Platform.fpk"
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
    & $Python (Join-Path $RepositoryRoot "scripts/normalize_fnos_package.py") $OutputPath
    if ($LASTEXITCODE -ne 0) {
        throw "fnOS package permission normalization failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
    Remove-ContainedTree $StagingPackage $StagingRoot
    Remove-Item -LiteralPath (Join-Path $StagingRoot "nexus-gateway.fpk") -Force -ErrorAction SilentlyContinue
}

$Digest = (Get-FileHash -LiteralPath $OutputPath -Algorithm SHA256).Hash.ToLowerInvariant()
$ChecksumManifest = Update-ChecksumManifest $ResolvedOutput $OutputName

Write-Host "Built $OutputPath"
Write-Host "Embedded native $($PlatformMetadata.Runtime) runtime $ResolvedRuntime"
Write-Host "Updated $ChecksumManifest"
Write-Host "SHA-256 $Digest"
