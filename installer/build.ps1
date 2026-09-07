<#
.SYNOPSIS
  Builds the pyMEP single installer (pyMEP_Setup_v<version>.exe).

.DESCRIPTION
  1. Stages pyMEP.extension from this repository (pycache and tests left out).
  2. Downloads the pinned pyRevit installer from the pyrevitlabs/pyRevit
     GitHub release (version in installer\pyrevit-version.txt, or -PyRevitVersion).
  3. Compiles installer\pyMEP.iss with Inno Setup 6 (ISCC.exe) into ..\dist.

.PARAMETER PyRevitVersion
  pyRevit release to bundle, e.g. 6.5.3. Defaults to installer\pyrevit-version.txt.

.PARAMETER PyRevitSetupPath
  Skip the download and use this pyRevit installer file instead.

.PARAMETER Iscc
  Path to ISCC.exe when it is not on PATH or in the default Inno Setup folder.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File installer\build.ps1
#>
[CmdletBinding()]
param(
    [string]$PyRevitVersion = "",
    [string]$PyRevitSetupPath = "",
    [string]$Iscc = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$here = $PSScriptRoot
$stage = Join-Path $here "stage"
$dist = Join-Path $root "dist"

# --- version -----------------------------------------------------------------
$verText = (Get-Content (Join-Path $root "pyMEP.extension\version.txt") -Raw).Trim()
$appVersion = $verText.TrimStart("v", "V")
if (-not $appVersion) { throw "pyMEP.extension\version.txt is empty" }
Write-Host "pyMEP version: $appVersion"

if (-not $PyRevitVersion) {
    $PyRevitVersion = (Get-Content (Join-Path $here "pyrevit-version.txt") -Raw).Trim()
}
Write-Host "pyRevit runtime: $PyRevitVersion"

# --- stage the extension -------------------------------------------------------
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Path $stage | Out-Null
$src = Join-Path $root "pyMEP.extension"
$dst = Join-Path $stage "pyMEP.extension"
robocopy $src $dst /E /XD __pycache__ .git /XF *.pyc /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed staging the extension ($LASTEXITCODE)" }
Write-Host "Staged $dst"

# --- pyRevit installer ------------------------------------------------------------
if ($PyRevitSetupPath) {
    if (-not (Test-Path $PyRevitSetupPath)) { throw "pyRevit installer not found: $PyRevitSetupPath" }
    $setupName = Split-Path -Leaf $PyRevitSetupPath
    Copy-Item $PyRevitSetupPath (Join-Path $stage $setupName) -Force
} else {
    $api = "https://api.github.com/repos/pyrevitlabs/pyRevit/releases/tags/v$PyRevitVersion"
    $headers = @{ "User-Agent" = "pyMEP-installer-build" }
    if ($env:GITHUB_TOKEN) { $headers["Authorization"] = "Bearer $env:GITHUB_TOKEN" }
    Write-Host "Looking up $api"
    $rel = Invoke-RestMethod -Uri $api -Headers $headers
    # The per-user pyRevit installer: pyRevit_<ver>_signed.exe (not the CLI, not the admin build).
    $asset = $rel.assets | Where-Object {
        $_.name -match '^pyRevit_.*signed\.exe$' -and $_.name -notmatch 'CLI' -and $_.name -notmatch 'admin'
    } | Select-Object -First 1
    if (-not $asset) {
        $names = ($rel.assets | ForEach-Object { $_.name }) -join ", "
        throw "No per-user pyRevit installer asset in release v$PyRevitVersion. Assets: $names"
    }
    $setupName = $asset.name
    $target = Join-Path $stage $setupName
    Write-Host "Downloading $setupName ($([math]::Round($asset.size / 1MB, 1)) MB)"
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $target -Headers $headers
}
Write-Host "pyRevit installer: $setupName"

# --- compile ---------------------------------------------------------------------
if (-not $Iscc) {
    $cand = @(
        (Get-Command ISCC.exe -ErrorAction SilentlyContinue | ForEach-Object { $_.Source }),
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    ) | Where-Object { $_ -and (Test-Path $_) }
    $Iscc = $cand | Select-Object -First 1
}
if (-not $Iscc) { throw "ISCC.exe (Inno Setup 6) not found. Install Inno Setup or pass -Iscc." }
New-Item -ItemType Directory -Path $dist -Force | Out-Null
& $Iscc "/DAppVersion=$appVersion" "/DPyRevitSetup=$setupName" (Join-Path $here "pyMEP.iss")
if ($LASTEXITCODE -ne 0) { throw "ISCC failed ($LASTEXITCODE)" }
$out = Join-Path $dist "pyMEP_Setup_v$appVersion.exe"
Write-Host "Built $out"
