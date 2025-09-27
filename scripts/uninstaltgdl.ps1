# uninstall-tgdl-clean.ps1
[CmdletBinding()]
param(
  [string]$Root = "D:\0ANTHONY SALVA\Rassel\tg_super_downloaderok"
)

$ErrorActionPreference = 'Stop'

function Info($m){ Write-Host $m -ForegroundColor Cyan }
function Ok($m){ Write-Host $m -ForegroundColor Green }
function Warn($m){ Write-Host $m -ForegroundColor Yellow }
function Err($m){ Write-Host $m -ForegroundColor Red }

if(-not (Test-Path -LiteralPath $Root)){ throw "No existe: $Root" }

# 1) Quitar SOLO nuestras tareas
try { Import-Module ScheduledTasks -ErrorAction SilentlyContinue } catch {}
Info "Eliminando tareas TGDL…"
foreach($t in @('tgdl-bot-task','tgdl-panel-task','aria2c-task')){
  try{ Stop-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue }catch{}
  try{ Unregister-ScheduledTask -TaskName $t -Confirm:$false -ErrorAction SilentlyContinue }catch{}
}
Ok "Tareas eliminadas (si existían)."

# 2) Matar procesos del proyecto (paréntesis correctos)
Info "Matando procesos del proyecto (bot/panel/aria2)…"
$procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
  $cl = $_.CommandLine
  if(-not $cl){ return $false }
  (
    ($cl -match 'tgdl\.cli\s+bot')   -or
    ($cl -match 'tgdl\.cli\s+panel') -or
    (($_.Name -ieq 'aria2c.exe') -and ($cl -match '--rpc-secret=D0wnl04d3r'))
  ) -and ($cl -match [regex]::Escape($Root))
}
if($procs){
  $procs | Select Name,ProcessId,ExecutablePath | Format-Table -Auto
  foreach($p in $procs){ try{ Stop-Process -Id $p.ProcessId -Force }catch{} }
  Ok "Procesos detenidos."
}else{
  Ok "No había procesos activos del proyecto."
}

# 3) Eliminar accesos directos TGDL en Escritorio e Inicio
Info "Eliminando accesos directos TGDL…"
$Desktop = [Environment]::GetFolderPath('Desktop')
$Startup = [Environment]::GetFolderPath('Startup')
$targets = @($Desktop,$Startup) | Where-Object { $_ }

foreach($base in $targets){
  Get-ChildItem -LiteralPath $base -Filter 'TGDL *.lnk' -ErrorAction SilentlyContinue |
    ForEach-Object {
      try{ Remove-Item -LiteralPath $_.FullName -Force }catch{}
    }
}
Ok "Accesos directos TGDL eliminados (si existían)."

# 4) Eliminar wrappers generados en scripts\
Info "Eliminando wrappers generados…"
$Scripts = Join-Path $Root 'scripts'
$toDelete = @(
  'start-bot.ps1','stop-bot.ps1','start-panel.ps1','stop-panel.ps1',
  'start-aria2.ps1','stop-aria2.ps1',
  'start-all.ps1','stop-all.ps1','logs-live.ps1',
  'start-bot.vbs','stop-bot.vbs','start-panel.vbs','stop-panel.vbs',
  'start-aria2.vbs','stop-aria2.vbs','start-all.vbs','stop-all.vbs',
  'tray.ps1','start-tray.vbs'
) | ForEach-Object { Join-Path $Scripts $_ }

foreach($p in $toDelete){
  if(Test-Path -LiteralPath $p){
    try{ Remove-Item -LiteralPath $p -Force }catch{}
  }
}
Ok "Wrappers limpiados."

# 5) Comprobación final
Info "Comprobación final:"
$leftTasks = @()
foreach($t in @('tgdl-bot-task','tgdl-panel-task','aria2c-task')){
  try{ if(Get-ScheduledTask -TaskName $t -ErrorAction Stop){ $leftTasks += $t } }catch{}
}
if($leftTasks.Count){ Warn ("Aún quedan tareas: {0}" -f ($leftTasks -join ', ')) } else { Ok "Sin tareas TGDL registradas." }

$still = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
  $cl = $_.CommandLine
  if(-not $cl){ return $false }
  (
    ($cl -match 'tgdl\.cli\s+bot')   -or
    ($cl -match 'tgdl\.cli\s+panel') -or
    (($_.Name -ieq 'aria2c.exe') -and ($cl -match '--rpc-secret=D0wnl04d3r'))
  ) -and ($cl -match [regex]::Escape($Root))
}
if($still){
  Warn "Procesos aún activos:"
  $still | Select Name,ProcessId,CommandLine | Format-Table -Auto
}else{
  Ok "Sin procesos TGDL activos."
}

Ok "Limpieza completada."
