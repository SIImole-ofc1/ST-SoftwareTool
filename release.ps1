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

$ErrorActionPreference = "Stop"
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

# ── 2. PyInstaller ────────────────────────────────────────────────────────────
Write-Host "`n[2/5] Building with PyInstaller..." -ForegroundColor Yellow
Set-Location $root
pyinstaller ST.spec --noconfirm 2>&1 | Where-Object { $_ -match "completed|ERROR" } | Select-Object -Last 3
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
Write-Host "  Done." -ForegroundColor Green

# ── 3. Inno Setup ─────────────────────────────────────────────────────────────
Write-Host "`n[3/5] Compiling installer with Inno Setup..." -ForegroundColor Yellow
$iscc = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
& $iscc "$root\ST-Setup.iss" 2>&1 | Where-Object { $_ -match "Successful|Error" }
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
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

$installer = "$root\dist\installer\ST-SoftwareTool-Setup.exe"
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
