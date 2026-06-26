# release.ps1 — Full automated release for ST-SoftwareTool
#
# Usage:  .\release.ps1 1.2.0
#
# What it does:
#   1. Bumps the version in updater.py and ST-Setup.iss
#   2. Builds the app with PyInstaller
#   3. Compiles the installer with Inno Setup
#   4. Creates a GitHub Release and uploads ST-SoftwareTool-Setup.exe
#   5. Commits and pushes all changes (triggers Cloudflare Pages deploy)

param(
    [Parameter(Mandatory=$true)]
    [string]$Version
)

$ErrorActionPreference = "Continue"
$root = "C:\ST"

Write-Host "`n=== ST-SoftwareTool Release Script ===" -ForegroundColor Cyan
Write-Host "Version: $Version`n" -ForegroundColor Cyan

# ── 1. Bump version ───────────────────────────────────────────────────────────
Write-Host "[1/5] Bumping version to $Version..." -ForegroundColor Yellow

$updater = "$root\core\updater.py"
(Get-Content $updater) -replace 'APP_VERSION = "[^"]+"', "APP_VERSION = `"$Version`"" |
    Set-Content $updater -Encoding UTF8

$iss = "$root\ST-Setup.iss"
(Get-Content $iss) -replace '#define AppVersion\s+"[^"]+"', "#define AppVersion   `"$Version`"" |
    Set-Content $iss -Encoding UTF8

Write-Host "  Done." -ForegroundColor Green

# ── 2. Nuitka (native compilation — no decompilable bytecode) ─────────────────
Write-Host "`n[2/5] Compiling with Nuitka (this takes ~40 min on first run)..." -ForegroundColor Yellow
Set-Location $root

# Clean previous Nuitka outputs
Remove-Item -Recurse -Force dist_nuitka -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force dist_nuitka_pm -ErrorAction SilentlyContinue

# Build main app
python -m nuitka `
    --standalone `
    --windows-console-mode=disable `
    --enable-plugin=pyside6 `
    --windows-icon-from-ico=assets/STsoftwareterminalLOGO.ico `
    --include-data-dir=assets=assets `
    --include-data-dir=core/tor_bundle=core/tor_bundle `
    --output-dir=dist_nuitka `
    --output-filename=ST.exe `
    --assume-yes-for-downloads `
    main.py 2>$null
if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: Nuitka main build failed (exit $LASTEXITCODE)" -ForegroundColor Red; exit 1 }

# Build proc_monitor subprocess
python -m nuitka `
    --standalone `
    --windows-console-mode=disable `
    --output-dir=dist_nuitka_pm `
    --output-filename=proc_monitor.exe `
    --assume-yes-for-downloads `
    core/proc_monitor.py 2>$null
if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: Nuitka proc_monitor build failed (exit $LASTEXITCODE)" -ForegroundColor Red; exit 1 }

# Merge proc_monitor into main app dist folder
# Copy all files (proc_monitor.exe + its C-extension DLLs like _psutil_windows.pyd)
# so proc_monitor.exe can find its dependencies at runtime
Copy-Item "dist_nuitka_pm\proc_monitor.dist\*" "dist_nuitka\main.dist\" -Recurse -Force

Write-Host "  Done." -ForegroundColor Green

# ── 3. Inno Setup ─────────────────────────────────────────────────────────────
Write-Host "`n[3/5] Compiling installer with Inno Setup..." -ForegroundColor Yellow
$iscc = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
& $iscc "$root\ST-Setup.iss" 2>$null
if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: Inno Setup failed" -ForegroundColor Red; exit 1 }
Write-Host "  Done." -ForegroundColor Green

# ── 4. GitHub Release ─────────────────────────────────────────────────────────
Write-Host "`n[4/5] Creating GitHub Release v$Version..." -ForegroundColor Yellow

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
$h  = @{ Authorization = "token $token"; Accept = "application/vnd.github.v3+json" }
$uh = @{ Authorization = "token $token"; "Content-Type" = "application/octet-stream" }

$body = @{
    tag_name = "v$Version"
    name     = "ST-SoftwareTool v$Version"
    body     = "ST-SoftwareTool v$Version`n`nDownload ST-SoftwareTool-Setup.exe below to install or upgrade."
} | ConvertTo-Json

$release = Invoke-RestMethod -Method Post `
    -Uri "https://api.github.com/repos/SIImole-ofc1/ST-SoftwareTool/releases" `
    -Headers $h -Body $body -ContentType "application/json"

$installer = "$root\dist_nuitka\installer\ST-SoftwareTool-Setup.exe"
$bytes = [System.IO.File]::ReadAllBytes($installer)
Write-Host "  Uploading $([Math]::Round($bytes.Length/1MB,1)) MB..." -ForegroundColor Yellow

$asset = Invoke-RestMethod -Method Post `
    -Uri "https://uploads.github.com/repos/SIImole-ofc1/ST-SoftwareTool/releases/$($release.id)/assets?name=ST-SoftwareTool-Setup.exe" `
    -Headers $uh -Body $bytes -TimeoutSec 300

Write-Host "  Live: $($asset.browser_download_url)" -ForegroundColor Green

# ── 5. Git commit + push ──────────────────────────────────────────────────────
Write-Host "`n[5/5] Committing and pushing..." -ForegroundColor Yellow
Set-Location $root
git add core/updater.py ST-Setup.iss index.html
git commit -m "Release v$Version"
git push origin main
Write-Host "  Done. Cloudflare Pages will deploy in ~1 minute." -ForegroundColor Green

Write-Host "`n=== Release v$Version complete! ===" -ForegroundColor Cyan
Write-Host "Website download: always up to date (uses /releases/latest/download/)" -ForegroundColor Cyan
Write-Host "In-app updater:   users on older versions will see popup on next launch`n" -ForegroundColor Cyan
