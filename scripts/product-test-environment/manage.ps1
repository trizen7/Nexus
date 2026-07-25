[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("deploy", "start", "stop", "status", "reset", "upgrade")]
    [string]$Command = "status"
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$Root = [System.IO.Path]::GetFullPath((Split-Path -Parent $MyInvocation.MyCommand.Path)).TrimEnd("\")
$ProductRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $Root)).TrimEnd("\")
$AppDir = Join-Path $Root "app"
$VenvDir = Join-Path $Root "venv"
$DataDir = Join-Path $Root "data"
$LogDir = Join-Path $Root "logs"
$StateDir = Join-Path $Root "state"
$ProcessFile = Join-Path $StateDir "process.json"
$DeploymentFile = Join-Path $StateDir "deployment.json"
$BindAddress = "0.0.0.0"
$HealthAddress = "127.0.0.1"
$Port = 18787
$BaseUrl = "http://${HealthAddress}:$Port"
$ManagedPorts = @(18787, 18788)
$ProcessRecoveryWindowSeconds = 10
$FirewallRuleNames = @(
    "Nexus Local Product Test Gateway 18787",
    "Nexus Local Product Test Gateway 18787-18788",
    "Nexus Local Product Test Gateway 18788",
    "Nexus Local Product Test Gateway 18787 HTTP"
)

function Assert-ManagedPath([string]$Path) {
    $full = [System.IO.Path]::GetFullPath($Path).TrimEnd("\")
    if (-not $full.StartsWith($Root + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify a path outside the deployed test environment: $full"
    }
    return $full
}

function Remove-ManagedTree([string]$Path) {
    $safe = Assert-ManagedPath $Path
    if (Test-Path -LiteralPath $safe) {
        Remove-Item -LiteralPath $safe -Recurse -Force
    }
}

function Get-VenvPython {
    return Join-Path $VenvDir "Scripts\python.exe"
}


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

function Find-NexusBootstrapPython {
    return Find-NexusPython ""
}

function Read-JsonFile([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try {
        return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Write-JsonFile([string]$Path, $Value) {
    $parent = Split-Path -Parent $Path
    $null = New-Item -ItemType Directory -Path $parent -Force
    $Value | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Get-ProcessStartTicks($Process) {
    try {
        return [Int64]$Process.StartTime.ToUniversalTime().Ticks
    } catch {
        return $null
    }
}

function Get-CimProcess([int]$ProcessId) {
    try {
        return Get-CimInstance Win32_Process `
            -Filter ("ProcessId = {0}" -f $ProcessId) `
            -ErrorAction Stop |
            Select-Object -First 1
    } catch {
        return $null
    }
}

function Get-GatewayCommandPort([string]$CommandLine) {
    if ([string]::IsNullOrWhiteSpace($CommandLine)) { return $null }
    $match = [regex]::Match(
        $CommandLine,
        '(?i)(?:^|\s)--port(?:=|\s+)(?<port>\d+)(?=\s|$)'
    )
    if (-not $match.Success) { return $null }
    $parsedPort = 0
    if (-not [int]::TryParse($match.Groups['port'].Value, [ref]$parsedPort)) { return $null }
    return $parsedPort
}

function Test-GatewayCommandLine([string]$CommandLine) {
    if ([string]::IsNullOrWhiteSpace($CommandLine)) { return $false }
    if (-not [regex]::IsMatch($CommandLine, '(?i)(?:^|\s)-m\s+"?nexus_gateway"?(?=\s|$)')) {
        return $false
    }
    $commandPort = Get-GatewayCommandPort $CommandLine
    if ($null -eq $commandPort) { return $false }
    return $ManagedPorts -contains [int]$commandPort
}

function Get-RecoverableGatewayChildren($Record) {
    $recordPid = 0
    $recordTicks = [Int64]0
    if (-not [int]::TryParse([string]$Record.pid, [ref]$recordPid)) { return @() }
    if (-not [Int64]::TryParse([string]$Record.start_ticks, [ref]$recordTicks)) { return @() }

    try {
        $children = @(Get-CimInstance Win32_Process `
            -Filter ("ParentProcessId = {0}" -f $recordPid) `
            -ErrorAction Stop)
    } catch {
        return @()
    }

    $maximumDifference = [TimeSpan]::FromSeconds($ProcessRecoveryWindowSeconds).Ticks
    $candidates = @()
    foreach ($child in $children) {
        $commandLine = [string]$child.CommandLine
        if (-not (Test-GatewayCommandLine $commandLine)) { continue }
        $candidateProcess = Get-Process -Id ([int]$child.ProcessId) -ErrorAction SilentlyContinue
        if ($null -eq $candidateProcess) { continue }
        $candidateTicks = Get-ProcessStartTicks $candidateProcess
        if ($null -eq $candidateTicks) { continue }
        $difference = [Math]::Abs([Int64]($candidateTicks - $recordTicks))
        if ($difference -gt $maximumDifference) { continue }
        $candidates += [pscustomobject]@{
            Process = $candidateProcess
            StartTicks = [Int64]$candidateTicks
            CommandLine = $commandLine
        }
    }
    return $candidates
}

function Adopt-GatewayProcess($Record, $Candidate) {
    $updated = [ordered]@{}
    foreach ($property in $Record.PSObject.Properties) {
        $updated[$property.Name] = $property.Value
    }
    $updated['schema_version'] = 2
    $updated['pid'] = [int]$Candidate.Process.Id
    $updated['start_ticks'] = ([Int64]$Candidate.StartTicks).ToString()
    $updated['started_at'] = $Candidate.Process.StartTime.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    Write-JsonFile $ProcessFile $updated
    return [pscustomobject]@{
        Record = [pscustomobject]$updated
        Process = $Candidate.Process
    }
}

function Get-OwnedProcess {
    $record = Read-JsonFile $ProcessFile
    if ($null -eq $record -or $null -eq $record.pid -or $null -eq $record.start_ticks) {
        Remove-Item -LiteralPath $ProcessFile -Force -ErrorAction SilentlyContinue
        return $null
    }

    $recordPid = 0
    $recordTicks = [Int64]0
    if (-not [int]::TryParse([string]$record.pid, [ref]$recordPid) -or
        -not [Int64]::TryParse([string]$record.start_ticks, [ref]$recordTicks)) {
        Remove-Item -LiteralPath $ProcessFile -Force -ErrorAction SilentlyContinue
        return $null
    }

    $exactProcess = Get-Process -Id $recordPid -ErrorAction SilentlyContinue
    $exactMatches = $false
    if ($null -ne $exactProcess) {
        $actualTicks = Get-ProcessStartTicks $exactProcess
        if ($null -ne $actualTicks -and [Int64]$actualTicks -eq $recordTicks) {
            $cimProcess = Get-CimProcess $recordPid
            if ($null -ne $cimProcess -and (Test-GatewayCommandLine ([string]$cimProcess.CommandLine))) {
                $exactMatches = $true
            }
        }
    }

    # Python virtual-environment launchers on Windows can spawn the real base-Python
    # process and then exit. Recover only one tightly constrained direct child.
    $candidates = @(Get-RecoverableGatewayChildren $record)
    if ($candidates.Count -gt 1) {
        throw 'Multiple possible Nexus Gateway child processes were found; no process was touched.'
    }
    if ($candidates.Count -eq 1) {
        return Adopt-GatewayProcess $record $candidates[0]
    }
    if ($exactMatches) {
        return [pscustomobject]@{ Record = $record; Process = $exactProcess }
    }

    $recordAgeTicks = [DateTime]::UtcNow.Ticks - $recordTicks
    $recoveryTicks = [TimeSpan]::FromSeconds($ProcessRecoveryWindowSeconds).Ticks
    if ($recordAgeTicks -ge 0 -and $recordAgeTicks -le $recoveryTicks) {
        return $null
    }

    Remove-Item -LiteralPath $ProcessFile -Force -ErrorAction SilentlyContinue
    return $null
}

function Wait-ForProcessExit([int]$ProcessId, [int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if ($null -eq (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) { return $true }
        Start-Sleep -Milliseconds 200
    }
    return $null -eq (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)
}

function Get-LanAddress {
    try {
        $route = Get-NetRoute -AddressFamily IPv4 -DestinationPrefix "0.0.0.0/0" -ErrorAction Stop |
            Where-Object { $_.State -eq "Alive" } |
            Sort-Object @{ Expression = { [int]$_.RouteMetric + [int]$_.InterfaceMetric } } |
            Select-Object -First 1
        if ($null -ne $route) {
            $address = Get-NetIPAddress -AddressFamily IPv4 -InterfaceIndex $route.ifIndex -ErrorAction Stop |
                Where-Object {
                    $_.AddressState -eq "Preferred" -and
                    $_.IPAddress -notlike "127.*" -and
                    $_.IPAddress -notlike "169.254.*"
                } |
                Select-Object -First 1
            if ($null -ne $address) { return [string]$address.IPAddress }
        }
    } catch {}
    return $null
}

function Get-LanUrl {
    $lanAddress = Get-LanAddress
    if ([string]::IsNullOrWhiteSpace($lanAddress)) { return $null }
    return "http://${lanAddress}:$Port"
}

function Ensure-FirewallRule {
    try {
        foreach ($name in $FirewallRuleNames) {
            $existingRules = @(Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue)
            foreach ($rule in $existingRules) {
                Remove-NetFirewallRule -InputObject $rule -ErrorAction Stop
            }
        }
        $null = New-NetFirewallRule `
            -DisplayName $FirewallRuleNames[-1] `
            -Description "Allow the isolated Nexus product test Gateway from the local subnet only." `
            -Direction Inbound `
            -Action Allow `
            -Enabled True `
            -Profile Any `
            -Protocol TCP `
            -LocalPort $Port `
            -RemoteAddress LocalSubnet `
            -ErrorAction Stop
    } catch {
        Write-Warning ("Could not configure the local-subnet firewall rule: " + $_.Exception.Message)
    }
}

function Invoke-HiddenTaskkill([int]$ProcessId, [switch]$Force) {
    $taskkillPath = Join-Path $env:SystemRoot "System32\taskkill.exe"
    $arguments = "/PID $ProcessId /T"
    if ($Force) { $arguments = "/F " + $arguments }

    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $taskkillPath
    $startInfo.Arguments = $arguments
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true

    $taskkillProcess = New-Object System.Diagnostics.Process
    $taskkillProcess.StartInfo = $startInfo
    try {
        if (-not $taskkillProcess.Start()) { return -1 }
        $stdoutTask = $taskkillProcess.StandardOutput.ReadToEndAsync()
        $stderrTask = $taskkillProcess.StandardError.ReadToEndAsync()
        if (-not $taskkillProcess.WaitForExit(10000)) {
            try { $taskkillProcess.Kill() } catch {}
            $taskkillProcess.WaitForExit()
            return -1
        }
        $null = $stdoutTask.GetAwaiter().GetResult()
        $null = $stderrTask.GetAwaiter().GetResult()
        return $taskkillProcess.ExitCode
    } finally {
        $taskkillProcess.Dispose()
    }
}

function Wait-ForManagedPortsClosed([int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        $openPorts = @($ManagedPorts | Where-Object { Test-PortOpen -TargetPort $_ })
        if ($openPorts.Count -eq 0) { return $true }
        Start-Sleep -Milliseconds 200
    }
    return @($ManagedPorts | Where-Object { Test-PortOpen -TargetPort $_ }).Count -eq 0
}

function Stop-Gateway([switch]$Quiet) {
    $owned = Get-OwnedProcess
    if ($null -eq $owned) {
        if (Test-Path -LiteralPath $ProcessFile) {
            throw 'The Gateway process record is still within its recovery window, but no unique owned process can be identified yet.'
        }
        if (-not $Quiet) { Write-Host "Gateway is stopped." }
        return
    }
    $pidValue = [int]$owned.Process.Id
    $null = Invoke-HiddenTaskkill -ProcessId $pidValue
    if (-not (Wait-ForProcessExit $pidValue 5)) {
        $null = Invoke-HiddenTaskkill -ProcessId $pidValue -Force
    }
    if (-not (Wait-ForProcessExit $pidValue 5)) {
        throw "Could not stop the owned Gateway process. No other process was touched."
    }
    Remove-Item -LiteralPath $ProcessFile -Force -ErrorAction SilentlyContinue
    if (-not (Wait-ForManagedPortsClosed 5)) {
        throw 'The owned Gateway process exited, but a managed port (18787 or 18788) is still listening; no unrelated process was touched.'
    }
    if (-not $Quiet) { Write-Host "Gateway stopped." }
}

function Test-PortOpen([int]$TargetPort) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect($HealthAddress, $TargetPort, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne(500, $false)) { return $false }
        $client.EndConnect($async)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Get-ListeningProcessIds([int]$TargetPort) {
    try {
        return @(Get-NetTCPConnection -State Listen -LocalPort $TargetPort -ErrorAction Stop |
            Select-Object -ExpandProperty OwningProcess -Unique)
    } catch {
        return @()
    }
}

function Invoke-HttpJson([string]$Url) {
    $python = Get-VenvPython
    if (-not (Test-Path -LiteralPath $python)) { return $null }
    $probeScript = @"
import json
import sys
import urllib.request

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open(sys.argv[1], timeout=3) as response:
    if response.status != 200:
        raise SystemExit(2)
    payload = json.load(response)
sys.stdout.write(json.dumps(payload, ensure_ascii=False))
"@
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = & $python -X utf8 -c $probeScript $Url 2>$null
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($exitCode -ne 0) { return $null }
    try {
        return (($output -join "`n") | ConvertFrom-Json)
    } catch {
        return $null
    }
}

function Get-LatestArtifact {
    $candidates = @(Get-ChildItem -LiteralPath $ProductRoot -Filter "Nexus-Gateway-*.zip" -File -Recurse |
        ForEach-Object {
            $match = [regex]::Match($_.Name, '^Nexus-Gateway-(?<version>\d+\.\d+\.\d+)\.zip$')
            if ($match.Success) {
                [pscustomobject]@{
                    Artifact = $_
                    Version = [version]$match.Groups['version'].Value
                }
            }
        } |
        Sort-Object Version, @{ Expression = { $_.Artifact.LastWriteTimeUtc } } -Descending)
    if ($candidates.Count -eq 0) { throw "No Nexus-Gateway-*.zip artifact was found in $ProductRoot" }
    return $candidates[0].Artifact
}

function Verify-Artifact($Artifact) {
    $actual = (Get-FileHash -LiteralPath $Artifact.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    $sumFile = Join-Path $Artifact.DirectoryName "SHA256SUMS.txt"
    if (Test-Path -LiteralPath $sumFile) {
        $escapedName = [regex]::Escape($Artifact.Name)
        $line = Get-Content -LiteralPath $sumFile -Encoding UTF8 |
            Where-Object { $_ -match "^([0-9a-fA-F]{64})\s+\*?$escapedName$" } |
            Select-Object -First 1
        if ($null -ne $line) {
            $expected = ([regex]::Match($line, "^[0-9a-fA-F]{64}").Value).ToLowerInvariant()
            if ($actual -ne $expected) { throw "Artifact checksum does not match SHA256SUMS.txt" }
        }
    }
    return $actual
}

function Ensure-Dependencies([string]$RequirementsPath) {
    $python = Get-VenvPython
    $requirementsHash = (Get-FileHash -LiteralPath $RequirementsPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $dependencyStateFile = Join-Path $StateDir "dependencies.json"
    $dependencyState = Read-JsonFile $dependencyStateFile
    $importsWork = $false
    if (Test-Path -LiteralPath $python -PathType Leaf) {
        & $python -c "import aiohttp, multidict, yarl" *> $null
        $importsWork = $LASTEXITCODE -eq 0
    }
    if ($importsWork -and $null -ne $dependencyState -and [string]$dependencyState.requirements_sha256 -eq $requirementsHash) {
        return
    }

    $bootstrapPython = Find-NexusBootstrapPython
    Remove-ManagedTree $VenvDir
    $pipCacheDir = Assert-ManagedPath (Join-Path $Root "cache\pip")
    $null = New-Item -ItemType Directory -Path $pipCacheDir -Force
    $null = New-Item -ItemType Directory -Path $StateDir -Force
    $lockFile = Join-Path $StateDir "requirements.lock.txt"
    $previousCache = [Environment]::GetEnvironmentVariable("PIP_CACHE_DIR", "Process")
    $previousVersionCheck = [Environment]::GetEnvironmentVariable("PIP_DISABLE_PIP_VERSION_CHECK", "Process")
    $previousNoInput = [Environment]::GetEnvironmentVariable("PIP_NO_INPUT", "Process")
    [Environment]::SetEnvironmentVariable("PIP_CACHE_DIR", $pipCacheDir, "Process")
    [Environment]::SetEnvironmentVariable("PIP_DISABLE_PIP_VERSION_CHECK", "1", "Process")
    [Environment]::SetEnvironmentVariable("PIP_NO_INPUT", "1", "Process")
    try {
        Write-Host "Creating isolated Python environment..."
        & $bootstrapPython -m venv $VenvDir
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $python -PathType Leaf)) {
            throw "Could not create the isolated Python environment."
        }
        Write-Host "Installing pinned release dependencies..."
        & $python -m pip install --disable-pip-version-check --no-input --requirement $RequirementsPath
        if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
        & $python -c "import aiohttp, multidict, yarl"
        if ($LASTEXITCODE -ne 0) { throw "The installed runtime dependencies cannot be imported." }
        $freeze = & $python -m pip freeze --all
        if ($LASTEXITCODE -ne 0) { throw "Could not record installed dependency versions." }
        ($freeze -join "`n") + "`n" | Set-Content -LiteralPath $lockFile -Encoding UTF8
        Write-JsonFile $dependencyStateFile ([ordered]@{
            schema_version = 1
            requirements_sha256 = $requirementsHash
            synced_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        })
    } finally {
        [Environment]::SetEnvironmentVariable("PIP_CACHE_DIR", $previousCache, "Process")
        [Environment]::SetEnvironmentVariable("PIP_DISABLE_PIP_VERSION_CHECK", $previousVersionCheck, "Process")
        [Environment]::SetEnvironmentVariable("PIP_NO_INPUT", $previousNoInput, "Process")
    }
}


function Deploy-LatestArtifact {
    $artifact = Get-LatestArtifact
    $artifactHash = Verify-Artifact $artifact
    $staging = Assert-ManagedPath (Join-Path $Root "app.next")
    $previous = Assert-ManagedPath (Join-Path $Root "app.previous")
    Remove-ManagedTree $staging
    Remove-ManagedTree $previous
    $null = New-Item -ItemType Directory -Path $staging
    Write-Host ("Extracting release artifact: " + $artifact.Name)
    Expand-Archive -LiteralPath $artifact.FullName -DestinationPath $staging -Force
    $requirements = Join-Path $staging "gateway\requirements.txt"
    $entryPoint = Join-Path $staging "gateway\nexus_gateway\__main__.py"
    if (-not (Test-Path -LiteralPath $requirements) -or -not (Test-Path -LiteralPath $entryPoint)) {
        Remove-ManagedTree $staging
        throw "The release artifact is incomplete."
    }
    Ensure-Dependencies $requirements
    if (Test-Path -LiteralPath $AppDir) { Move-Item -LiteralPath $AppDir -Destination $previous }
    try {
        Move-Item -LiteralPath $staging -Destination $AppDir
        Remove-ManagedTree $previous
    } catch {
        if (Test-Path -LiteralPath $AppDir) { Remove-ManagedTree $AppDir }
        if (Test-Path -LiteralPath $previous) { Move-Item -LiteralPath $previous -Destination $AppDir }
        throw
    }
    Write-JsonFile $DeploymentFile ([ordered]@{
        schema_version = 1
        artifact = $artifact.Name
        artifact_sha256 = $artifactHash
        deployed_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        gateway_url = $BaseUrl
        lan_gateway_url = Get-LanUrl
        bind_address = $BindAddress
    })
    Write-Host "Release artifact deployed independently from source."
}

function Get-SetupStatus {
    return Invoke-HttpJson ($BaseUrl + "/api/setup/status")
}

function Wait-ForSetupStatus {
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline) {
        $owned = Get-OwnedProcess
        if ($null -eq $owned -and -not (Test-Path -LiteralPath $ProcessFile)) {
            throw "Gateway exited during startup. Check the files in $LogDir"
        }
        $status = Get-SetupStatus
        if ($null -ne $status) {
            $confirmed = Get-OwnedProcess
            if ($null -ne $confirmed) { return $status }
        }
        Start-Sleep -Milliseconds 400
    }
    throw "Gateway did not become ready. Check the files in $LogDir"
}

function Start-Gateway {
    $owned = Get-OwnedProcess
    if ($null -ne $owned) {
        $httpReady = Test-PortOpen -TargetPort $Port
        $legacyHttpsOpen = Test-PortOpen -TargetPort 18788
        if ($httpReady -and -not $legacyHttpsOpen -and $null -ne (Get-SetupStatus)) {
            Write-Host ("Gateway is already running at " + $BaseUrl)
            $existingLanUrl = Get-LanUrl
            if ($null -ne $existingLanUrl) { Write-Host ("LAN HTTP: " + $existingLanUrl) }
            return
        }
        Write-Host "The owned Gateway does not match the HTTP-origin listener policy; restarting it safely..."
        Stop-Gateway -Quiet
    }
    if (-not (Test-Path -LiteralPath (Join-Path $AppDir "gateway\nexus_gateway\__main__.py"))) {
        Deploy-LatestArtifact
    }
    Ensure-Dependencies (Join-Path $AppDir "gateway\requirements.txt")
    if (Test-PortOpen -TargetPort $Port) { throw "HTTP port $Port is already used by another process; it was not stopped." }
    if (Test-PortOpen -TargetPort 18788) { throw "Legacy HTTPS port 18788 is still used by another process; HTTP-origin startup was refused." }
    $null = New-Item -ItemType Directory -Path $DataDir -Force
    $null = New-Item -ItemType Directory -Path (Join-Path $DataDir "media") -Force
    $null = New-Item -ItemType Directory -Path $LogDir -Force
    $null = New-Item -ItemType Directory -Path $StateDir -Force
    Ensure-FirewallRule
    $python = Get-VenvPython
    $workingDirectory = Join-Path $AppDir "gateway"
    $stdoutLog = Join-Path $LogDir "gateway.stdout.log"
    $stderrLog = Join-Path $LogDir "gateway.stderr.log"
    $launchEnvironment = [ordered]@{
        PYTHONUTF8 = "1"
        PYTHONUNBUFFERED = "1"
        PYTHONNOUSERSITE = "1"
        NEXUS_GATEWAY_HOST = $BindAddress
        NEXUS_GATEWAY_PORT = $Port.ToString()
        NEXUS_CREDENTIALS_FILE = (Join-Path $DataDir "account.json")
        NEXUS_CONFIG_FILE = (Join-Path $DataDir "config.json")
        NEXUS_BOOTSTRAP_TOKEN_FILE = (Join-Path $DataDir "bootstrap.token")
        NEXUS_MEDIA_DIR = (Join-Path $DataDir "media")
    }
    $blockedEnvironment = @(
        "NEXUS_USERNAME", "NEXUS_PASSWORD", "NEXUS_SESSION_SECRET",
        "HERMES_API_URL", "HERMES_API_TOKEN", "NEXUS_LOCAL_HERMES_TOKEN",
        "PYTHONPATH", "PYTHONHOME"
    )
    $allNames = @($launchEnvironment.Keys) + $blockedEnvironment
    $previousEnvironment = @{}
    foreach ($name in $allNames) {
        $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
        if ($launchEnvironment.Contains($name)) {
            [Environment]::SetEnvironmentVariable($name, [string]$launchEnvironment[$name], "Process")
        } else {
            [Environment]::SetEnvironmentVariable($name, $null, "Process")
        }
    }
    try {
        $process = Start-Process -FilePath $python `
            -ArgumentList @("-m", "nexus_gateway", "--host", $BindAddress, "--port", $Port.ToString()) `
            -WorkingDirectory $workingDirectory `
            -RedirectStandardOutput $stdoutLog `
            -RedirectStandardError $stderrLog `
            -WindowStyle Hidden `
            -PassThru
    } finally {
        foreach ($name in $allNames) {
            [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], "Process")
        }
    }
    $launcherTicks = Get-ProcessStartTicks $process
    if ($null -eq $launcherTicks) { throw "Gateway launcher metadata could not be read. Check the files in $LogDir" }
    Write-JsonFile $ProcessFile ([ordered]@{
        schema_version = 2
        pid = [int]$process.Id
        start_ticks = ([Int64]$launcherTicks).ToString()
        started_at = $process.StartTime.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        gateway_url = $BaseUrl
        lan_gateway_url = Get-LanUrl
        bind_address = $BindAddress
    })
    try {
        $setup = Wait-ForSetupStatus
        $owned = Get-OwnedProcess
        if ($null -eq $owned) { throw "The running Gateway process could not be identified safely." }
        if (-not (Test-PortOpen -TargetPort $Port)) { throw "HTTP port $Port did not open." }
        if (Test-PortOpen -TargetPort 18788) { throw "Legacy HTTPS port 18788 is still listening; HTTP-origin verification failed." }
        $listenerPids = @(Get-ListeningProcessIds -TargetPort $Port)
        if ($listenerPids.Count -ne 1 -or [int]$listenerPids[0] -ne [int]$owned.Process.Id) {
            throw "HTTP port $Port is not owned by the safely tracked Gateway process."
        }
    } catch {
        Stop-Gateway -Quiet
        throw
    }
    Write-Host ("HTTP Gateway started at " + $BaseUrl)
    $lanUrl = Get-LanUrl
    if ($null -ne $lanUrl) { Write-Host ("LAN HTTP: " + $lanUrl) }
    if ([bool]$setup.initialized) {
        Write-Host "State: initialized"
    } else {
        Write-Host "State: waiting for first-time setup"
        Write-Host ("Bootstrap token file: " + (Join-Path $DataDir "bootstrap.token"))
    }
}

function Show-Status {
    $deployment = Read-JsonFile $DeploymentFile
    $owned = Get-OwnedProcess
    Write-Host ("Deployment directory: " + $Root)
    if ($null -ne $deployment) { Write-Host ("Artifact: " + [string]$deployment.artifact) }
    Write-Host ("Gateway process: " + $(if ($null -ne $owned) { "running" } else { "stopped" }))
    Write-Host ("Local HTTP: " + $BaseUrl)
    $lanUrl = Get-LanUrl
    if ($null -ne $lanUrl) { Write-Host ("LAN HTTP: " + $lanUrl) }
    Write-Host ("HTTP port 18787: " + $(if (Test-PortOpen -TargetPort $Port) { "listening" } else { "closed" }))
    Write-Host ("Legacy HTTPS port 18788: " + $(if (Test-PortOpen -TargetPort 18788) { "unexpectedly listening" } else { "closed" }))
    if ($null -ne $owned) {
        $setup = Get-SetupStatus
        if ($null -eq $setup) {
            Write-Host "Service state: unreachable"
        } else {
            $initialized = [bool]$setup.initialized
            $setupAvailable = (-not $initialized) -and (Test-Path -LiteralPath (Join-Path $DataDir "bootstrap.token"))
            Write-Host ("Initialized: " + $initialized)
            Write-Host ("Setup available: " + $setupAvailable)
        }
    }
}

function Reset-Environment {
    Stop-Gateway -Quiet
    Remove-ManagedTree $DataDir
    Remove-ManagedTree $LogDir
    Remove-Item -LiteralPath $ProcessFile -Force -ErrorAction SilentlyContinue
    Write-Host "Runtime data, account, password, configuration, media, historical TLS files and logs were cleared."
    Start-Gateway
}

try {
    switch ($Command) {
        "deploy" {
            Stop-Gateway -Quiet
            Deploy-LatestArtifact
        }
        "start" { Start-Gateway }
        "stop" { Stop-Gateway }
        "status" { Show-Status }
        "reset" { Reset-Environment }
        "upgrade" {
            Stop-Gateway -Quiet
            Deploy-LatestArtifact
            Start-Gateway
        }
    }
} catch {
    Write-Error ("Local product test environment failed: " + $_.Exception.Message)
    exit 1
}
