# release.ps1 — Full automated release for ST-SoftwareTool
#
# Usage:  .\release.ps1 1.2.0
#
# What it does:
#   1. Bumps the version in updater.py and ST-Setup.iss
#   2. Builds the app with Nuitka
#   3. Compiles the installer with Inno Setup
#   4. Commits and pushes all changes (triggers Cloudflare Pages deploy)
#   5. Creates a GitHub Release on that commit and uploads ST-SoftwareTool-Setup.exe
#      (GitHub Actions then builds the macOS .dmg files and attaches them)

param(
    [Parameter(Mandatory=$true)]
    [string]$Version
)

$ErrorActionPreference = "Continue"
$root = $PSScriptRoot

Write-Host "`n=== ST-SoftwareTool Release Script ===" -ForegroundColor Cyan
Write-Host "Version: $Version`n" -ForegroundColor Cyan

# ── 1. Bump version ───────────────────────────────────────────────────────────
Write-Host "[1/5] Bumping version to $Version..." -ForegroundColor Yellow

# Always read as UTF-8: Windows PowerShell 5.1 otherwise assumes ANSI and mangles non-ASCII text.
$updater = "$root\core\updater.py"
(Get-Content $updater -Encoding UTF8) -replace 'APP_VERSION = "[^"]+"', "APP_VERSION = `"$Version`"" |
    Set-Content $updater -Encoding UTF8

$iss = "$root\ST-Setup.iss"
(Get-Content $iss -Encoding UTF8) -replace '#define AppVersion\s+"[^"]+"', "#define AppVersion   `"$Version`"" |
    Set-Content $iss -Encoding UTF8

$html = "$root\index.html"
(Get-Content $html -Raw -Encoding UTF8) -replace '<span class="gh-version">[^<]*</span>', "<span class=""gh-version"">v$Version</span>" |
    Set-Content $html -Encoding UTF8

$latest = "$root\download\latest.json"
[ordered]@{
    version = $Version
    url     = "https://github.com/SIImole-ofc1/ST-SoftwareTool/releases/download/v$Version/ST-SoftwareTool-Setup.exe"
} | ConvertTo-Json | ForEach-Object {
    # No BOM: JSON parsers (e.g. Python's json.loads) reject a leading BOM
    [System.IO.File]::WriteAllText($latest, $_ + "`n", (New-Object System.Text.UTF8Encoding $false))
}

Write-Host "  Done." -ForegroundColor Green

# ── 2. Nuitka (native compilation — no decompilable bytecode) ─────────────────
Write-Host "`n[2/5] Compiling with Nuitka (this takes ~40 min on first run)..." -ForegroundColor Yellow
Set-Location $root

# Find Python: PATH first, then the usual per-user / all-users install folders
$python = (Get-Command python.exe -ErrorAction SilentlyContinue |
           Where-Object { $_.Source -notmatch 'WindowsApps' } | Select-Object -First 1).Source
if (-not $python) {
    $python = @("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
                "C:\Program Files\Python312\python.exe") |
              Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $python) { Write-Host "ERROR: Python 3.12 not found" -ForegroundColor Red; exit 1 }
Write-Host "  Using Python: $python"

# Clean previous Nuitka outputs
Remove-Item -Recurse -Force dist_nuitka -ErrorAction SilentlyContinue

# Seal the app's pictures into one encrypted file; its key is compiled into the app
& $python scripts/pack_assets.py build/pack
if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: packing the app's pictures failed" -ForegroundColor Red; exit 1 }

# Build main app
& $python -m nuitka `
    --standalone `
    --windows-console-mode=disable `
    --enable-plugin=pyside6 `
    --windows-icon-from-ico=assets/STsoftwareterminalLOGO.ico `
    --include-data-files=build/pack/app.pack=assets/app.pack `
    --include-data-dir=core/tor_bundle=core/tor_bundle `
    --include-data-dir=tools=tools `
    --output-dir=dist_nuitka `
    --output-filename=ST.exe `
    --assume-yes-for-downloads `
    main.py 2>$null
if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: Nuitka main build failed (exit $LASTEXITCODE)" -ForegroundColor Red; exit 1 }

# Build proc_monitor — psutil runs here so the main process stays GIL-free
& $python -m nuitka `
    --standalone `
    --windows-console-mode=attach `
    --output-dir=dist_nuitka\proc_monitor_build `
    --output-filename=proc_monitor.exe `
    --assume-yes-for-downloads `
    core\proc_monitor.py 2>$null
if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: Nuitka proc_monitor build failed (exit $LASTEXITCODE)" -ForegroundColor Red; exit 1 }

# Copy proc_monitor.exe into the main app distribution folder
Copy-Item "dist_nuitka\proc_monitor_build\proc_monitor.dist\proc_monitor.exe" `
          "dist_nuitka\main.dist\proc_monitor.exe" -Force

# Nuitka --include-data-dir silently skips .exe and .dll files from data dirs.
# Manually copy the full tor_bundle so tor.exe and tor-gencert.exe are included.
Remove-Item "dist_nuitka\main.dist\core\tor_bundle" -Recurse -Force -ErrorAction SilentlyContinue
Copy-Item "core\tor_bundle" "dist_nuitka\main.dist\core\" -Recurse -Force

Write-Host "  Done." -ForegroundColor Green

# ── 3. Inno Setup ─────────────────────────────────────────────────────────────
Write-Host "`n[3/5] Compiling installer with Inno Setup..." -ForegroundColor Yellow
$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") |
        Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { Write-Host "ERROR: Inno Setup 6 not found" -ForegroundColor Red; exit 1 }
& $iscc "$root\ST-Setup.iss" 2>$null
if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: Inno Setup failed" -ForegroundColor Red; exit 1 }
Write-Host "  Done." -ForegroundColor Green

# ── 4. Git commit + push (before the release, so its tag points at this commit) ─
# The macOS build (GitHub Actions) checks out the release tag: it must contain the bump.
Write-Host "`n[4/5] Committing and pushing..." -ForegroundColor Yellow
Set-Location $root
git add core/updater.py ST-Setup.iss index.html release.ps1 main.py download/latest.json
git add core/commands.py ui/terminal_widget.py core/av_*.py ui/main_window.py
git add tools/yt-dlp.exe 2>$null
git add .gitignore 2>$null
git commit -m "Release v$Version"
git push origin main
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: git push failed - pull the remote changes, then run this script again." -ForegroundColor Red
    exit 1
}
$releaseSha = (git rev-parse HEAD).Trim()
Write-Host "  Pushed $releaseSha. Cloudflare Pages will deploy in ~1 minute." -ForegroundColor Green

# ── 5. GitHub Release ─────────────────────────────────────────────────────────
Write-Host "`n[5/5] Creating GitHub Release v$Version..." -ForegroundColor Yellow

Add-Type -TypeDefinition @'
using System; using System.Runtime.InteropServices;
public class CredManR {
    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
    public struct CREDENTIAL {
        public int Flags; public int Type; public string TargetName;
        public string Comment; public System.Runtime.InteropServices.ComTypes.FILETIME LastWritten;
        public int CredentialBlobSize; public IntPtr CredentialBlob;
        public int Persist; public int AttributeCount; public IntPtr Attributes;
        public string TargetAlias; public string UserName;
    }
    [DllImport("advapi32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern bool CredRead(string target, int type, int flags, out IntPtr credential);
    [DllImport("advapi32.dll")] static extern void CredFree(IntPtr cred);
    public static string GetPassword(string target) {
        IntPtr ptr;
        if (!CredRead(target, 1, 0, out ptr)) return null;
        var c = (CREDENTIAL)Marshal.PtrToStructure(ptr, typeof(CREDENTIAL));
        var pwd = Marshal.PtrToStringUni(c.CredentialBlob, c.CredentialBlobSize/2);
        CredFree(ptr); return pwd;
    }
}
'@

$token = [CredManR]::GetPassword("git:https://github.com")
if (-not $token) { Write-Host "ERROR: GitHub token not found in Credential Manager" -ForegroundColor Red; exit 1 }
$h  = @{ Authorization = "token $token"; Accept = "application/vnd.github.v3+json" }
$uh = @{ Authorization = "token $token"; "Content-Type" = "application/octet-stream" }

# Check if the release already exists; create it only if it doesn't
$releaseId = $null
try {
    $existing  = Invoke-RestMethod `
        -Uri "https://api.github.com/repos/SIImole-ofc1/ST-SoftwareTool/releases/tags/v$Version" `
        -Headers $h
    $releaseId = $existing.id
    Write-Host "  Release v$Version already exists (id=$releaseId) - replacing installer." -ForegroundColor Yellow
    # Delete existing installer asset so the new one can be uploaded
    foreach ($a in $existing.assets) {
        if ($a.name -eq "ST-SoftwareTool-Setup.exe") {
            Invoke-RestMethod -Method Delete `
                -Uri "https://api.github.com/repos/SIImole-ofc1/ST-SoftwareTool/releases/assets/$($a.id)" `
                -Headers $h | Out-Null
        }
    }
} catch {
    $body = @{
        tag_name         = "v$Version"
        target_commitish = $releaseSha
        name             = "ST-SoftwareTool v$Version"
        body             = "ST-SoftwareTool v$Version`n`n" +
                           "- Windows: ST-SoftwareTool-Setup.exe`n" +
                           "- macOS (Apple Silicon): ST-SoftwareTool-macOS-arm64.dmg`n" +
                           "- macOS (Intel): ST-SoftwareTool-macOS-x86_64.dmg`n`n" +
                           "The macOS files are added automatically about 30-45 minutes after publishing."
    } | ConvertTo-Json
    $release   = Invoke-RestMethod -Method Post `
        -Uri "https://api.github.com/repos/SIImole-ofc1/ST-SoftwareTool/releases" `
        -Headers $h -Body $body -ContentType "application/json"
    $releaseId = $release.id
    Write-Host "  Created new release v$Version (id=$releaseId)." -ForegroundColor Green
}

$installer = "$root\dist_nuitka\installer\ST-SoftwareTool-Setup.exe"
$bytes = [System.IO.File]::ReadAllBytes($installer)
Write-Host "  Uploading $([Math]::Round($bytes.Length/1MB,1)) MB..." -ForegroundColor Yellow

$asset = Invoke-RestMethod -Method Post `
    -Uri "https://uploads.github.com/repos/SIImole-ofc1/ST-SoftwareTool/releases/$releaseId/assets?name=ST-SoftwareTool-Setup.exe" `
    -Headers $uh -Body $bytes -TimeoutSec 300

Write-Host "  Live: $($asset.browser_download_url)" -ForegroundColor Green

Write-Host "`n=== Release v$Version complete! ===" -ForegroundColor Cyan
Write-Host "Website download: always up to date (uses /releases/latest/download/)" -ForegroundColor Cyan
Write-Host "In-app updater:   users on older versions will see popup on next launch`n" -ForegroundColor Cyan
