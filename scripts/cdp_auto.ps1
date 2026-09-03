# Tu dong: copy profile -> mo Chrome debug rieng -> chay probe -> doc ket qua
param(
  [string]$ProfileName = 'Profile 15',
  [int]$Port = 9333,
  [string]$Item = '52065517811'
)
$ErrorActionPreference = 'Stop'
$src = "C:\Users\Administrator\AppData\Local\Google\Chrome\User Data"
$dst = "D:\Shopee_PH\artifacts\cdp_profile"
$srcProfile = Join-Path $src $ProfileName
if (-not (Test-Path $srcProfile)) { Write-Host "KHONG THAY profile: $srcProfile"; exit 2 }
Write-Host "[1/6] Xoa ban copy cu + copy CHI profile $ProfileName (+ Local State)..."
if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
New-Item -ItemType Directory -Force -Path $dst | Out-Null
# Local State (chua key ma hoa cookie cua Chrome) phai co o root user-data-dir
if (Test-Path (Join-Path $src 'Local State')) {
  Copy-Item (Join-Path $src 'Local State') (Join-Path $dst 'Local State') -Force
}
$dstProfile = Join-Path $dst $ProfileName
New-Item -ItemType Directory -Force -Path $dstProfile | Out-Null
$excl = @('Cache','Code Cache','GPUCache','DawnCache','GrShaderCache','ShaderCache','component_crx_cache','graphite-database','DawnGraphiteCache','Crashpad')
robocopy $srcProfile $dstProfile /E /NFL /NDL /NJH /NJS /NP /XD @($excl) | Out-Null
$rc = $LASTEXITCODE
if ($rc -ge 8) { Write-Host "robocopy loi ($rc)"; exit $rc }
Write-Host "[2/6] Bo khoa profile trong ban copy..."
foreach ($f in @('Lock','SingletonCookie','SingletonLock','SingletonSocket')) {
  $p = Join-Path $dstProfile $f
  if (Test-Path $p) { Remove-Item -Force $p }
}
$p2 = Join-Path $dst 'SingletonLock'
if (Test-Path $p2) { Remove-Item -Force $p2 }
Write-Host "[3/6] Mo Chrome debug (port $Port) voi profile copy..."
$chrome = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
$p = Start-Process -FilePath $chrome -ArgumentList @(
  "--remote-debugging-port=$Port",
  "--user-data-dir=$dst",
  "--profile-directory=$ProfileName",
  '--no-first-run','--no-default-browser-check',
  'https://affiliate.shopee.ph/'
) -PassThru
Write-Host "Chrome PID: $($p.Id)"
Write-Host "[4/6] Cho CDP san sang..."
$ok = $false
for ($i = 0; $i -lt 40; $i++) {
  Start-Sleep -Milliseconds 500
  try {
    $v = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/json/version" -TimeoutSec 2
    if ($v.Browser) { $ok = $true; Write-Host "CDP OK: $($v.Browser)"; break }
  } catch {}
}
if (-not $ok) { Write-Host "KHONG mo duoc CDP port $Port"; exit 3 }
Write-Host "[5/6] Cho trang affiliate load 12s..."
Start-Sleep -Seconds 12
Write-Host "[6/6] Chay probe..."
node (Join-Path (Split-Path $PSScriptRoot -Parent) 'scripts\cdp_probe.mjs') --port $Port --item $Item
Write-Host "=== XONG. Ket qua tai artifacts\cdp_probe_result.json ==="
