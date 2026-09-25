# Starts Email Prioritizer for this laptop only: a hidden server on 127.0.0.1 and one dedicated Chrome app window.
# The server keeps running after the window is closed, so reopening is instant (run stop_local.ps1 to stop it).
# Server output goes to local-server.log in the project folder.
$ErrorActionPreference = 'Stop'
trap { "$(Get-Date -Format s) launcher error: $_" | Add-Content (Join-Path (Split-Path -Parent $PSScriptRoot) 'local-server.log'); exit 1 }

# only one launcher at a time, however fast the shortcut is double-clicked
$createdNew = $false
$lock = New-Object System.Threading.Mutex($true, 'Global\EmailPrioritizerLauncher', [ref]$createdNew)
if (-not $createdNew) { exit 0 }
$Port = 47613
$Root = Split-Path -Parent $PSScriptRoot
$Url = "http://localhost:$Port"
$Chrome = "$env:ProgramFiles\Google\Chrome\Application\chrome.exe"
$Profile = Join-Path $env:LOCALAPPDATA 'EmailPrioritizer\chrome-profile'   # its own Chrome: never joins your other windows

function Test-Health {
    try { Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$Port/healthz" -TimeoutSec 2 | Out-Null; $true } catch { $false }
}
function Get-AppWindow {
    Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" | Where-Object { $_.CommandLine -like "*$Profile*" }
}

# one window only: if the app window is already open, do nothing
if (Get-AppWindow) { exit 0 }

if (-not (Test-Health)) {
    $env:PUBLIC_BASE_URL = $Url
    $env:GOOGLE_REDIRECT_URI = "$Url/api/accounts/google/callback"
    $env:MICROSOFT_REDIRECT_URI = "$Url/api/accounts/microsoft/callback"
    $env:SESSION_HTTPS_ONLY = 'false'
    $env:DATABASE_URL = "sqlite:///$($Root.Replace([string][char]92, '/'))/app.db"   # local SQLite, not the cloud database in .env
    $cmd = "python -m uvicorn app.main:app --host 127.0.0.1 --port $Port >> local-server.log 2>&1"
    Start-Process cmd.exe -ArgumentList '/c', $cmd -WorkingDirectory $Root -WindowStyle Hidden
    # a cold start takes a while: open the window at once on a "Starting..." page that hands over when the server is up
    $Target = ([Uri](Join-Path $PSScriptRoot 'loading.html')).AbsoluteUri
} else {
    $Target = $Url
}

New-Item -ItemType Directory -Force $Profile | Out-Null
Start-Process $Chrome -ArgumentList "--app=$Target", "--user-data-dir=`"$Profile`"", '--no-first-run', '--no-default-browser-check', '--window-size=1400,900',
    # keep this window light: one renderer, no extensions, no sync or background services
    '--renderer-process-limit=1', '--disable-extensions', '--disable-sync', '--disable-background-networking', '--disable-component-update', '--disable-default-apps', '--no-pings', '--disable-breakpad'
