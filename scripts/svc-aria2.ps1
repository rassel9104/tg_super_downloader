# svc-aria2.ps1
$ErrorActionPreference = 'Stop'

$root = "C:\tools\tg_super_downloaderok"
$env:USERPROFILE = Join-Path $root "svc\home"
$env:HOME        = $env:USERPROFILE
$env:TEMP        = Join-Path $root "svc\tmp"
$env:TMP         = $env:TEMP

$null = New-Item -Force -ItemType Directory $env:USERPROFILE, $env:TEMP, (Join-Path $root "downloads") | Out-Null

Set-Location $root
$aria = (Get-Command aria2c.exe -ErrorAction Stop).Source
& $aria --enable-rpc=true --rpc-listen-all=false --rpc-listen-port=6800 `
  --check-certificate=false --file-allocation=none `
  --max-connection-per-server=16 --split=16 --continue=true `
  --rpc-secret=D0wnl04d3r --dir="$root\downloads"
