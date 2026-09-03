# Dò profile nao con phiên affiliate thuc su (offer/product tra code 0)
param(
  [string]$Item = '41060972359',
  [string[]]$Profiles = @('Profile 18','Profile 15','Profile 12','Profile 16','Profile 7','Profile 2','Profile 6','Profile 17','Profile 1')
)
$chrome = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
$ud = 'C:\Users\Administrator\AppData\Local\Google\Chrome\User Data'
$root = 'D:\Shopee_PH'
function Kill-DebugChrome {
  Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'remote-debugging-port=9333' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
  Start-Sleep -Seconds 2
}
foreach ($prof in $Profiles) {
  Write-Host "=== Thu profile: $prof ==="
  Kill-DebugChrome
  Start-Process -FilePath $chrome -ArgumentList @('--remote-debugging-port=9333', "--user-data-dir=$ud", "--profile-directory=$prof", '--no-first-run','--no-default-browser-check', 'https://affiliate.shopee.ph/')
  $ok = $false
  for ($i=0; $i -lt 40; $i++) { Start-Sleep -Milliseconds 750; try { $v = Invoke-RestMethod -Uri 'http://127.0.0.1:9333/json/version' -TimeoutSec 2; if ($v.Browser) { $ok=$true; break } } catch {} }
  if (-not $ok) { Write-Host "  (khong mo duoc CDP)"; continue }
  Start-Sleep -Seconds 12
  node "$root\scripts\cdp_probe.mjs" --port 9333 --item $Item --out "$root\artifacts\cdp_probe_result.json" | Out-Null
  $res = $null
  try { $res = Get-Content "$root\artifacts\cdp_probe_result.json" -Raw | ConvertFrom-Json } catch {}
  $first = $res.results | Select-Object -First 1
  if ($first -and $first.bodyHead -match '"code"\s*:\s*0') {
    Write-Host ">>> TIM THAY PROFILE HOAT DONG: $prof"
    Write-Host "KET QUA:"
    node "$root\scripts\cdp_probe.mjs" --port 9333 --item $Item
    exit 0
  }
  Write-Host "  (khong qua - chuyen profile khac)"
}
Write-Host "KHONG co profile nao qua duoc."
