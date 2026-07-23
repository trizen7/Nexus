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
$HttpsPort = 18788
$BaseUrl = "http://${HealthAddress}:$Port"
$HttpsBaseUrl = "https://${HealthAddress}:$HttpsPort"
$TlsDir = Join-Path $DataDir "tls"
$FirewallRuleNames = @(
    "Nexus Local Product Test Gateway 18787",
    "Nexus Local Product Test Gateway 18787-18788"
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

function Get-OwnedProcess {
    $record = Read-JsonFile $ProcessFile
    if ($null -eq $record -or $null -eq $record.pid -or $null -eq $record.start_ticks) {
        Remove-Item -LiteralPath $ProcessFile -Force -ErrorAction SilentlyContinue
        return $null
    }
    $process = Get-Process -Id ([int]$record.pid) -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        Remove-Item -LiteralPath $ProcessFile -Force -ErrorAction SilentlyContinue
        return $null
    }
    try {
        $actualTicks = $process.StartTime.ToUniversalTime().Ticks.ToString()
    } catch {
        Remove-Item -LiteralPath $ProcessFile -Force -ErrorAction SilentlyContinue
        return $null
    }
    if ($actualTicks -ne [string]$record.start_ticks) {
        Remove-Item -LiteralPath $ProcessFile -Force -ErrorAction SilentlyContinue
        return $null
    }
    return [pscustomobject]@{ Record = $record; Process = $process }
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

function Get-LanHttpsUrl {
    $lanAddress = Get-LanAddress
    if ([string]::IsNullOrWhiteSpace($lanAddress)) { return $null }
    return "https://${lanAddress}:$HttpsPort"
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
            -LocalPort @($Port, $HttpsPort) `
            -RemoteAddress LocalSubnet `
            -ErrorAction Stop
    } catch {
        Write-Warning ("Could not configure the local-subnet firewall rule: " + $_.Exception.Message)
    }
}

function Get-OpenSsl {
    $candidates = @(
        "C:\Program Files\Git\usr\bin\openssl.exe",
        "C:\Program Files\Git\mingw64\bin\openssl.exe"
    )
    $command = Get-Command openssl.exe -ErrorAction SilentlyContinue
    if ($null -ne $command) { $candidates = @($command.Source) + $candidates }
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    throw "OpenSSL was not found. Install Git for Windows before enabling the local HTTPS environment."
}

function Invoke-OpenSsl([string]$OpenSsl, [string[]]$Arguments) {
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        # OpenSSL writes normal key-generation progress to stderr. Windows PowerShell 5
        # turns redirected native stderr into ErrorRecord objects, so keep it non-terminating
        # and decide success strictly from the native process exit code.
        $ErrorActionPreference = "Continue"
        $output = & $OpenSsl @Arguments 2>&1
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($exitCode -ne 0) {
        $message = ($output | Select-Object -Last 5) -join " "
        throw "OpenSSL failed: $message"
    }
}

function Ensure-TlsCertificates {
    $safeTlsDir = Assert-ManagedPath $TlsDir
    $null = New-Item -ItemType Directory -Path $safeTlsDir -Force
    $caKey = Join-Path $safeTlsDir "ca.key"
    $caCert = Join-Path $safeTlsDir "ca.crt"
    $serverKey = Join-Path $safeTlsDir "server.key"
    $serverCert = Join-Path $safeTlsDir "server.crt"
    $sanState = Join-Path $safeTlsDir "server-san.txt"
    $caKeyExists = Test-Path -LiteralPath $caKey
    $caCertExists = Test-Path -LiteralPath $caCert
    if ($caKeyExists -xor $caCertExists) {
        throw "The local HTTPS CA is incomplete. Restore both ca.key and ca.crt instead of replacing the trusted CA silently."
    }

    $openSsl = Get-OpenSsl
    if (-not $caKeyExists) {
        Write-Host "Creating the persistent Nexus local test CA..."
        Invoke-OpenSsl $openSsl @(
            "req", "-x509", "-newkey", "rsa:3072", "-sha256", "-days", "3650", "-nodes",
            "-keyout", $caKey, "-out", $caCert,
            "-subj", "/CN=Nexus Local Test CA",
            "-addext", "basicConstraints=critical,CA:TRUE",
            "-addext", "keyUsage=critical,keyCertSign,cRLSign"
        )
    }

    $sanEntries = @("DNS:localhost", "IP:127.0.0.1")
    $lanAddress = Get-LanAddress
    if (-not [string]::IsNullOrWhiteSpace($lanAddress)) { $sanEntries += "IP:$lanAddress" }
    $sanValue = $sanEntries -join ","
    $savedSan = if (Test-Path -LiteralPath $sanState) {
        (Get-Content -LiteralPath $sanState -Raw -Encoding UTF8).Trim()
    } else { "" }
    $serverCurrent = (Test-Path -LiteralPath $serverKey) -and
        (Test-Path -LiteralPath $serverCert) -and
        ($savedSan -eq $sanValue)
    if (-not $serverCurrent) {
        Write-Host "Issuing the local HTTPS server certificate for the current LAN address..."
        $csr = Join-Path $StateDir "server.csr"
        $ext = Join-Path $StateDir "server.ext"
        $serial = Join-Path $safeTlsDir "ca.srl"
        $null = New-Item -ItemType Directory -Path $StateDir -Force
        @(
            "basicConstraints=critical,CA:FALSE",
            "keyUsage=critical,digitalSignature,keyEncipherment",
            "extendedKeyUsage=serverAuth",
            "subjectAltName=$sanValue"
        ) | Set-Content -LiteralPath $ext -Encoding ASCII
        Remove-Item -LiteralPath $csr, $serial -Force -ErrorAction SilentlyContinue
        try {
            Invoke-OpenSsl $openSsl @(
                "req", "-new", "-newkey", "rsa:2048", "-sha256", "-nodes",
                "-keyout", $serverKey, "-out", $csr,
                "-subj", "/CN=Nexus Local Test Gateway"
            )
            Invoke-OpenSsl $openSsl @(
                "x509", "-req", "-in", $csr, "-sha256", "-days", "825",
                "-CA", $caCert, "-CAkey", $caKey, "-CAcreateserial",
                "-out", $serverCert, "-extfile", $ext
            )
            Invoke-OpenSsl $openSsl @("verify", "-CAfile", $caCert, $serverCert)
            Set-Content -LiteralPath $sanState -Value $sanValue -Encoding UTF8
        } finally {
            Remove-Item -LiteralPath $csr, $ext, $serial -Force -ErrorAction SilentlyContinue
        }
    }
    return [pscustomobject]@{
        Ca = $caCert
        Certificate = $serverCert
        Key = $serverKey
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

function Stop-Gateway([switch]$Quiet) {
    $owned = Get-OwnedProcess
    if ($null -eq $owned) {
        if (-not $Quiet) { Write-Host "Gateway is stopped." }
        return
    }
    $pidValue = [int]$owned.Record.pid
    $null = Invoke-HiddenTaskkill -ProcessId $pidValue
    if (-not (Wait-ForProcessExit $pidValue 5)) {
        $null = Invoke-HiddenTaskkill -ProcessId $pidValue -Force
    }
    if (-not (Wait-ForProcessExit $pidValue 5)) {
        throw "Could not stop the owned Gateway process. No other process was touched."
    }
    Remove-Item -LiteralPath $ProcessFile -Force -ErrorAction SilentlyContinue
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

function Get-LatestArtifact {
    $artifact = Get-ChildItem -LiteralPath $ProductRoot -Filter "Nexus-Gateway-*.zip" -File |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 1
    if ($null -eq $artifact) { throw "No Nexus-Gateway-*.zip artifact was found in $ProductRoot" }
    return $artifact
}

function Verify-Artifact($Artifact) {
    $actual = (Get-FileHash -LiteralPath $Artifact.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    $sumFile = Join-Path $ProductRoot "SHA256SUMS.txt"
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
    $uvCommand = Get-Command uv -ErrorAction SilentlyContinue
    if ($null -eq $uvCommand) { throw "uv is required but was not found in PATH." }
    $python = Get-VenvPython
    if (-not (Test-Path -LiteralPath $python)) {
        Write-Host "Creating isolated Python environment..."
        & $uvCommand.Source venv $VenvDir --python python
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $python)) {
            throw "Could not create the isolated Python environment."
        }
    }
    $null = New-Item -ItemType Directory -Path $StateDir -Force
    $lockFile = Join-Path $StateDir "requirements.lock.txt"
    Write-Host "Resolving and synchronizing release dependencies in copy mode..."
    $previousLinkMode = [Environment]::GetEnvironmentVariable("UV_LINK_MODE", "Process")
    $previousProgress = [Environment]::GetEnvironmentVariable("UV_NO_PROGRESS", "Process")
    [Environment]::SetEnvironmentVariable("UV_LINK_MODE", "copy", "Process")
    [Environment]::SetEnvironmentVariable("UV_NO_PROGRESS", "1", "Process")
    try {
        & $uvCommand.Source pip compile $RequirementsPath --python $python --output-file $lockFile --quiet
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $lockFile)) {
            throw "Dependency resolution failed."
        }
        & $uvCommand.Source pip sync --python $python --link-mode copy $lockFile
        if ($LASTEXITCODE -ne 0) { throw "Dependency synchronization failed." }
        & $python -c "import aiohttp, multidict, yarl"
        if ($LASTEXITCODE -ne 0) { throw "The synchronized runtime dependencies cannot be imported." }
    } finally {
        [Environment]::SetEnvironmentVariable("UV_LINK_MODE", $previousLinkMode, "Process")
        [Environment]::SetEnvironmentVariable("UV_NO_PROGRESS", $previousProgress, "Process")
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
        https_url = $HttpsBaseUrl
        lan_https_url = Get-LanHttpsUrl
        bind_address = $BindAddress
    })
    Write-Host "Release artifact deployed independently from source."
}

function Get-SetupStatus {
    try {
        return Invoke-RestMethod -Method Get -Uri ($BaseUrl + "/api/setup/status") -TimeoutSec 3
    } catch {
        return $null
    }
}

function Wait-ForSetupStatus([int]$ProcessId) {
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline) {
        if ($null -eq (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) {
            throw "Gateway exited during startup. Check the files in $LogDir"
        }
        $status = Get-SetupStatus
        if ($null -ne $status) { return $status }
        Start-Sleep -Milliseconds 400
    }
    throw "Gateway did not become ready. Check the files in $LogDir"
}

function Start-Gateway {
    $owned = Get-OwnedProcess
    if ($null -ne $owned) {
        $httpReady = Test-PortOpen -TargetPort $Port
        $httpsReady = Test-PortOpen -TargetPort $HttpsPort
        if ($httpReady -and $httpsReady) {
            Write-Host ("Gateway is already running. HTTP API: " + $BaseUrl)
            Write-Host ("HTTPS Web: " + $HttpsBaseUrl)
            $existingLanUrl = Get-LanUrl
            $existingLanHttpsUrl = Get-LanHttpsUrl
            if ($null -ne $existingLanUrl) { Write-Host ("LAN HTTP API: " + $existingLanUrl) }
            if ($null -ne $existingLanHttpsUrl) { Write-Host ("LAN HTTPS Web: " + $existingLanHttpsUrl) }
            return
        }
        Write-Host "The owned Gateway is missing the current HTTP/HTTPS listeners; restarting it safely..."
        Stop-Gateway -Quiet
    }
    if (-not (Test-Path -LiteralPath (Join-Path $AppDir "gateway\nexus_gateway\__main__.py"))) {
        Deploy-LatestArtifact
    }
    Ensure-Dependencies (Join-Path $AppDir "gateway\requirements.txt")
    if (Test-PortOpen -TargetPort $Port) { throw "Port $Port is already used by another process; it was not stopped." }
    if (Test-PortOpen -TargetPort $HttpsPort) { throw "Port $HttpsPort is already used by another process; it was not stopped." }
    $null = New-Item -ItemType Directory -Path $DataDir -Force
    $null = New-Item -ItemType Directory -Path (Join-Path $DataDir "media") -Force
    $null = New-Item -ItemType Directory -Path $LogDir -Force
    $null = New-Item -ItemType Directory -Path $StateDir -Force
    $tls = Ensure-TlsCertificates
    Ensure-FirewallRule
    $python = Get-VenvPython
    $workingDirectory = Join-Path $AppDir "gateway"
    $stdoutLog = Join-Path $LogDir "gateway.stdout.log"
    $stderrLog = Join-Path $LogDir "gateway.stderr.log"
    $launchEnvironment = [ordered]@{
        PYTHONUTF8 = "1"
        PYTHONUNBUFFERED = "1"
        NEXUS_GATEWAY_HOST = $BindAddress
        NEXUS_GATEWAY_PORT = $Port.ToString()
        NEXUS_HTTPS_PORT = $HttpsPort.ToString()
        NEXUS_TLS_CERT_FILE = $tls.Certificate
        NEXUS_TLS_KEY_FILE = $tls.Key
        NEXUS_TLS_CA_FILE = $tls.Ca
        NEXUS_REDIRECT_WEB_TO_HTTPS = "true"
        NEXUS_CREDENTIALS_FILE = (Join-Path $DataDir "account.json")
        NEXUS_CONFIG_FILE = (Join-Path $DataDir "config.json")
        NEXUS_BOOTSTRAP_TOKEN_FILE = (Join-Path $DataDir "bootstrap.token")
        NEXUS_MEDIA_DIR = (Join-Path $DataDir "media")
    }
    $blockedEnvironment = @(
        "NEXUS_USERNAME", "NEXUS_PASSWORD", "NEXUS_SESSION_SECRET",
        "HERMES_API_URL", "HERMES_API_TOKEN", "NEXUS_LOCAL_HERMES_TOKEN"
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
    Start-Sleep -Milliseconds 150
    $process.Refresh()
    if ($process.HasExited) { throw "Gateway failed to start. Check the files in $LogDir" }
    Write-JsonFile $ProcessFile ([ordered]@{
        schema_version = 1
        pid = $process.Id
        start_ticks = $process.StartTime.ToUniversalTime().Ticks.ToString()
        started_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        gateway_url = $BaseUrl
        lan_gateway_url = Get-LanUrl
        https_url = $HttpsBaseUrl
        lan_https_url = Get-LanHttpsUrl
        bind_address = $BindAddress
    })
    try {
        $setup = Wait-ForSetupStatus $process.Id
    } catch {
        Stop-Gateway -Quiet
        throw
    }
    Write-Host ("HTTP API started at " + $BaseUrl)
    Write-Host ("HTTPS Web started at " + $HttpsBaseUrl)
    $lanUrl = Get-LanUrl
    $lanHttpsUrl = Get-LanHttpsUrl
    if ($null -ne $lanUrl) { Write-Host ("LAN HTTP API: " + $lanUrl) }
    if ($null -ne $lanHttpsUrl) { Write-Host ("LAN HTTPS Web: " + $lanHttpsUrl) }
    Write-Host ("Local CA certificate: " + $tls.Ca)
    Write-Host ("Local CA SHA-256: " + (Get-CertificateSha256 $tls.Ca))
    Write-Host ("Local CA download: " + $BaseUrl + "/nexus-local-ca.crt")
    if ($null -ne $lanUrl) { Write-Host ("LAN CA download: " + $lanUrl + "/nexus-local-ca.crt") }
    if ([bool]$setup.initialized) {
        Write-Host "State: initialized"
    } else {
        Write-Host "State: waiting for first-time setup"
        Write-Host ("Bootstrap token file: " + (Join-Path $DataDir "bootstrap.token"))
    }
}

function Get-CertificateSha256([string]$Path) {
    $certificate = Get-PfxCertificate -FilePath $Path
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $hash = $sha256.ComputeHash($certificate.RawData)
    } finally {
        $sha256.Dispose()
    }
    return ([System.BitConverter]::ToString($hash)).Replace("-", "")
}

function Show-Status {
    $deployment = Read-JsonFile $DeploymentFile
    $owned = Get-OwnedProcess
    Write-Host ("Deployment directory: " + $Root)
    if ($null -ne $deployment) { Write-Host ("Artifact: " + [string]$deployment.artifact) }
    Write-Host ("Gateway process: " + $(if ($null -ne $owned) { "running" } else { "stopped" }))
    Write-Host ("Local HTTP API: " + $BaseUrl)
    Write-Host ("Local HTTPS Web: " + $HttpsBaseUrl)
    $lanUrl = Get-LanUrl
    $lanHttpsUrl = Get-LanHttpsUrl
    if ($null -ne $lanUrl) { Write-Host ("LAN HTTP API: " + $lanUrl) }
    if ($null -ne $lanHttpsUrl) { Write-Host ("LAN HTTPS Web: " + $lanHttpsUrl) }
    $caCertificate = Join-Path $TlsDir "ca.crt"
    if (Test-Path -LiteralPath $caCertificate) {
        Write-Host ("HTTPS CA: " + $caCertificate)
        Write-Host ("HTTPS CA SHA-256: " + (Get-CertificateSha256 $caCertificate))
        Write-Host ("Local CA download: " + $BaseUrl + "/nexus-local-ca.crt")
        if ($null -ne $lanUrl) { Write-Host ("LAN CA download: " + $lanUrl + "/nexus-local-ca.crt") }
    } else {
        Write-Host "HTTPS CA: not generated yet"
    }
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
    Write-Host "Runtime data, account, password, configuration, media, local HTTPS CA and logs were cleared."
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
