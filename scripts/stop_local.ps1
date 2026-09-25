# Stops the Email Prioritizer server (and closes its window).
$Port = 47613
$Profile = Join-Path $env:LOCALAPPDATA 'EmailPrioritizer\chrome-profile'
Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" | Where-Object { $_.CommandLine -like "*$Profile*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
$conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($conn) { taskkill /PID $conn.OwningProcess /T /F | Out-Null }
