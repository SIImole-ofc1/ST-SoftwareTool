# Build a clean Cloudflare Pages publish folder (website only).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$publish = Join-Path $root "publish"

if (Test-Path $publish) { Remove-Item $publish -Recurse -Force }
New-Item -ItemType Directory -Path $publish | Out-Null

Copy-Item (Join-Path $root "index.html") $publish
Copy-Item (Join-Path $root "style.css") $publish
Copy-Item (Join-Path $root "app.js") $publish
Copy-Item (Join-Path $root "assets") (Join-Path $publish "assets") -Recurse
Copy-Item (Join-Path $root "privacy") (Join-Path $publish "privacy") -Recurse
Copy-Item (Join-Path $root "download") (Join-Path $publish "download") -Recurse

# Ensure /privacy and /privacy/ resolve (CF Pages serves privacy/index.html)
Write-Host "Publish folder ready:"
Get-ChildItem $publish -Recurse | Select-Object FullName, Length
