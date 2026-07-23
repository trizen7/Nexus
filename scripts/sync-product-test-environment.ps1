[CmdletBinding()]
param(
    [string]$Destination
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$RepoRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot)).TrimEnd("\")
$ProductRoot = [System.IO.Path]::GetFullPath((Join-Path $RepoRoot "成品")).TrimEnd("\")
$TemplateRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "product-test-environment")).TrimEnd("\")
if ([string]::IsNullOrWhiteSpace($Destination)) {
    $Destination = Join-Path $ProductRoot "本地测试环境"
}
$Destination = [System.IO.Path]::GetFullPath($Destination).TrimEnd("\")
if ($Destination -ne $ProductRoot -and -not $Destination.StartsWith($ProductRoot + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to sync control files outside the product directory: $Destination"
}

$ControlFiles = @(
    "manage.ps1",
    "manage.cmd",
    "01-打开初始化页面.cmd",
    "02-查看状态.cmd",
    "03-停止测试环境.cmd",
    "04-清空并重新开始.cmd",
    "05-升级到最新成品.cmd",
    "06-查看反向代理说明.cmd",
    "使用说明.txt"
)

$ObsoleteControlFiles = @(
    "06-安装本机HTTPS证书.cmd"
)

$tokens = $null
$errors = $null
$null = [System.Management.Automation.Language.Parser]::ParseFile(
    (Join-Path $TemplateRoot "manage.ps1"),
    [ref]$tokens,
    [ref]$errors
)
if ($errors.Count -gt 0) {
    throw "Template manage.ps1 has PowerShell syntax errors."
}

$null = New-Item -ItemType Directory -Path $Destination -Force
foreach ($name in $ObsoleteControlFiles) {
    $obsolete = Join-Path $Destination $name
    if (Test-Path -LiteralPath $obsolete -PathType Leaf) {
        Remove-Item -LiteralPath $obsolete -Force
    }
}

foreach ($name in $ControlFiles) {
    $source = Join-Path $TemplateRoot $name
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Missing test-environment control template: $source"
    }
    Copy-Item -LiteralPath $source -Destination (Join-Path $Destination $name) -Force
}

Write-Host ("Synced " + $ControlFiles.Count + " control files to " + $Destination)
Write-Host "Runtime directories app, data, venv, logs and state were not modified."
