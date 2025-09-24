#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName System.Windows.Forms | Out-Null
Add-Type -AssemblyName System.Drawing        | Out-Null

try {
  # --- Rutas y transcript ---
  $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
  $RepoRoot  = Resolve-Path (Join-Path $ScriptDir "..")
  $LogsDir   = Join-Path $RepoRoot "logs"
  $IconsDir  = Join-Path $ScriptDir "icons"
  New-Item -ItemType Directory -Force -Path $LogsDir | Out-Null

  $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
  $TranscriptPath = Join-Path $LogsDir "tray-manager-$stamp.log"
  Start-Transcript -Path $TranscriptPath -Append | Out-Null

  if ([Threading.Thread]::CurrentThread.ApartmentState -ne 'STA') {
    [System.Windows.Forms.MessageBox]::Show("Este script debe ejecutarse en modo STA. Usa launch_tray.vbs.","TGDL")
    throw "No STA"
  }

  # --- Utils ---
  function Sanitize([string]$val){
    if (-not $val) { return $null }
    ($val.Trim() -replace '^\s*["'']|["'']\s*$', '')
  }

  function Read-DotEnv {
    $envFile = Join-Path $RepoRoot ".env"
    if (Test-Path $envFile) {
      Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*PANEL_HOST\s*=\s*(.+)$')   { $script:PANEL_HOST   = Sanitize $Matches[1] }
        if ($_ -match '^\s*PANEL_PORT\s*=\s*(.+)$')   { $script:PANEL_PORT   = Sanitize $Matches[1] }
        if ($_ -match '^\s*ARIA2_JSONRPC\s*=\s*(.+)$'){ $script:ARIA2_JSONRPC = Sanitize $Matches[1] }
        if ($_ -match '^\s*ARIA2_SECRET\s*=\s*(.+)$') { $script:ARIA2_SECRET  = Sanitize $Matches[1] }
        if ($_ -match '^\s*DOWNLOAD_DIR\s*=\s*(.+)$') { $script:DOWNLOAD_DIR  = Sanitize $Matches[1] }
      }
    }
  }

  Read-DotEnv
  # Defaults seguros (no variables “undefined” con StrictMode)
  if (-not (Get-Variable -Name PANEL_HOST    -Scope Script -ErrorAction SilentlyContinue)) { $script:PANEL_HOST   = "127.0.0.1" }
  if (-not (Get-Variable -Name PANEL_PORT    -Scope Script -ErrorAction SilentlyContinue)) { $script:PANEL_PORT   = "8080" }
  if (-not (Get-Variable -Name DOWNLOAD_DIR  -Scope Script -ErrorAction SilentlyContinue)) { $script:DOWNLOAD_DIR = (Join-Path $RepoRoot "data\downloads") }
  if (-not (Get-Variable -Name ARIA2_JSONRPC -Scope Script -ErrorAction SilentlyContinue)) { $script:ARIA2_JSONRPC = "http://127.0.0.1:6800/jsonrpc" }
  if (-not (Get-Variable -Name ARIA2_SECRET  -Scope Script -ErrorAction SilentlyContinue)) { $script:ARIA2_SECRET  = $null }

  # URL Panel (IPv6 con corchetes)
  $hostForUrl = $PANEL_HOST
  if ($hostForUrl -match ":") { $hostForUrl = "[" + $hostForUrl.Trim("[]") + "]" }
  $PanelUrl = 'http://{0}:{1}/' -f $hostForUrl, $PANEL_PORT

  # Puerto de ARIA2 desde ARIA2_JSONRPC
  $script:ARIA2_PORT = 6800
  try {
    if ($ARIA2_JSONRPC -match ':(\d+)/jsonrpc') { $script:ARIA2_PORT = [int]$Matches[1] }
  } catch {}

  # Python y aria2
  $PyLocal  = Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"
  $Python   = if (Test-Path $PyLocal) { $PyLocal } else { "python" }

  function Find-Aria2 {
    $p = (Get-Command aria2c.exe -ErrorAction SilentlyContinue | Select-Object -First 1).Source
    if ($p) { return $p }
    $choco = "C:\ProgramData\chocolatey\bin\aria2c.exe"
    if (Test-Path $choco) { return $choco }
    return $null
  }
  $Aria2Exe = Find-Aria2

  # --- Estado global ---
  $Global:ProcBot   = $null
  $Global:ProcPanel = $null
  $Global:ProcAria2 = $null
  $Global:LogBot    = $null
  $Global:LogPanel  = $null
  $Global:LogAria2  = $null

  # Lanzador/stop genérico con redirección a archivos (más fiable)
  function Start-LoggedProcess([string]$exe, [string]$argLine, [string]$name, [ref]$procRef, [ref]$logRef){
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    # Archivo “combinado” de referencia (lo seguimos guardando si lo usas en UI)
    $logRef.Value = Join-Path $LogsDir ("{0}-{1}.log" -f $name, $stamp)
    # Archivos separados requeridos por Start-Process
    $outPath = Join-Path $LogsDir ("{0}-{1}.out.log" -f $name, $stamp)
    $errPath = Join-Path $LogsDir ("{0}-{1}.err.log" -f $name, $stamp)

    try {
      $p = Start-Process -FilePath $exe `
                         -ArgumentList $argLine `
                         -WorkingDirectory $RepoRoot `
                         -WindowStyle Hidden `
                         -RedirectStandardOutput $outPath `
                         -RedirectStandardError  $errPath `
                         -PassThru
      $procRef.Value = $p

      # (Opcional) concatenar al archivo “combinado” de referencia
      try {
        "== STDOUT -> $outPath ==" | Out-File -FilePath $logRef.Value -Encoding UTF8
        Get-Content -LiteralPath $outPath | Add-Content -LiteralPath $logRef.Value
        "`n== STDERR -> $errPath ==" | Add-Content -LiteralPath $logRef.Value
        if (Test-Path $errPath) { Get-Content -LiteralPath $errPath | Add-Content -LiteralPath $logRef.Value }
      } catch {}

      return $true
    }
    catch {
      $procRef.Value = $null
      $stamp2    = Get-Date -Format "yyyyMMdd-HHmmss"
      $CrashPath = Join-Path $LogsDir ("tray-manager-crash-{0}.log" -f $stamp2)
      $line = "Start-Process failed for {0}: {1}" -f $name, $_.Exception.Message
      Set-Content -LiteralPath $CrashPath -Value $line -Encoding UTF8
      [System.Windows.Forms.MessageBox]::Show(("No se pudo iniciar {0}: {1}" -f $name, $_.Exception.Message), "TGDL")
      return $false
    }
  }
  function Stop-LoggedProcess([ref]$procRef){
    try {
      if ($procRef.Value -and -not $procRef.Value.HasExited) {
        $procRef.Value.Kill()
        Start-Sleep -Milliseconds 200
      }
    } catch {}
    $procRef.Value = $null
  }

  # --- Servicios ---
  function Start-Bot {
    if ($Global:ProcBot -and -not $Global:ProcBot.HasExited) { return }
    $ok = Start-LoggedProcess $Python "-m tgdl.cli bot" "bot" ([ref]$Global:ProcBot) ([ref]$Global:LogBot)
    if ($ok) { $ni.ShowBalloonTip(1500, "TGDL", "Bot iniciado", [System.Windows.Forms.ToolTipIcon]::Info) }
  }

  function Stop-Bot    { Stop-LoggedProcess ([ref]$Global:ProcBot) ; $ni.ShowBalloonTip(1200,"TGDL","Bot detenido",[System.Windows.Forms.ToolTipIcon]::None) }
  function Restart-Bot { Stop-Bot; Start-Bot }

  function Start-Panel {
    if ($Global:ProcPanel -and -not $Global:ProcPanel.HasExited) { return }
    $ok = Start-LoggedProcess $Python "-m tgdl.cli panel" "panel" ([ref]$Global:ProcPanel) ([ref]$Global:LogPanel)
    if ($ok) { $ni.ShowBalloonTip(1500, "TGDL", "Panel iniciado", [System.Windows.Forms.ToolTipIcon]::Info) }
  }

  function Stop-Panel   { Stop-LoggedProcess ([ref]$Global:ProcPanel) ; $ni.ShowBalloonTip(1200,"TGDL","Panel detenido",[System.Windows.Forms.ToolTipIcon]::None) }
  function Restart-Panel{ Stop-Panel; Start-Panel }

  function Start-Aria2 {
    if (-not $Aria2Exe) {
      [System.Windows.Forms.MessageBox]::Show("No se encontró aria2c.exe. Instálalo (choco install aria2) o añádelo al PATH.","TGDL")
      return
    }
    if ($Global:ProcAria2 -and -not $Global:ProcAria2.HasExited) { return }

    $args = @(
      "--enable-rpc=true",
      "--rpc-listen-all=false",
      "--rpc-listen-port=$ARIA2_PORT",
      "--check-certificate=false",
      "--file-allocation=none",
      "--max-connection-per-server=16",
      "--split=16",
      "--continue=true",
      "--dir=""$DOWNLOAD_DIR"""
    )
    if ($ARIA2_SECRET) { $args += "--rpc-secret=$ARIA2_SECRET" }
    $argLine = ($args -join ' ')  # ← construir UNA sola cadena

    $ok = Start-LoggedProcess $Aria2Exe $argLine "aria2" ([ref]$Global:ProcAria2) ([ref]$Global:LogAria2)
    if ($ok) { $ni.ShowBalloonTip(1500, "TGDL", "aria2 iniciado", [System.Windows.Forms.ToolTipIcon]::Info) }
  }

  function Stop-Aria2    { Stop-LoggedProcess ([ref]$Global:ProcAria2) ; $ni.ShowBalloonTip(1200,"TGDL","aria2 detenido",[System.Windows.Forms.ToolTipIcon]::None) }
  function Restart-Aria2 { Stop-Aria2; Start-Aria2 }

  function Start-All { Start-Aria2; Start-Panel; Start-Bot }
  function Stop-All  { Stop-Bot; Stop-Panel; Stop-Aria2 }

  # --- NotifyIcon + Menú (sin emojis) ---
  $ni = New-Object System.Windows.Forms.NotifyIcon
  $icoBot   = Join-Path $IconsDir "bot.ico"
  $ni.Icon  = if (Test-Path $icoBot) { [System.Drawing.Icon]::ExtractAssociatedIcon($icoBot) } else { [System.Drawing.Icon]::ExtractAssociatedIcon("$PSHOME\powershell.exe") }
  $ni.Visible = $true
  $ni.Text = "TGDL Manager"

  $menu = New-Object System.Windows.Forms.ContextMenuStrip
  $botItem    = $menu.Items.Add("Bot")
  $panelItem  = $menu.Items.Add("Panel")
  $aria2Item  = $menu.Items.Add("aria2")
  $menu.Items.Add("-") | Out-Null
  $menu.Items.Add("Start All").add_Click({ Start-All }) | Out-Null
  $menu.Items.Add("Stop All").add_Click({ Stop-All })   | Out-Null
  $menu.Items.Add("-") | Out-Null
  $menu.Items.Add("Abrir carpeta logs").add_Click({ if (Test-Path $LogsDir) { Invoke-Item $LogsDir } }) | Out-Null
  $menu.Items.Add("Abrir panel en navegador").add_Click({ Start-Process $PanelUrl }) | Out-Null
  $menu.Items.Add("-") | Out-Null
  $menu.Items.Add("Salir").add_Click({
    try { Stop-All } catch {}
    $ni.Visible = $false
    $ni.Dispose()
    try { Stop-Transcript | Out-Null } catch {}
    Stop-Process -Id $PID -Force
  }) | Out-Null

  # Submenús
  $botMenu = New-Object System.Windows.Forms.ContextMenuStrip
  $botMenu.Items.Add("Iniciar").add_Click({ Start-Bot })       | Out-Null
  $botMenu.Items.Add("Reiniciar").add_Click({ Restart-Bot })   | Out-Null
  $botMenu.Items.Add("Detener").add_Click({ Stop-Bot })        | Out-Null
  $botMenu.Items.Add("Estado").add_Click({
    $state = if ($Global:ProcBot -and -not $Global:ProcBot.HasExited) { "Ejecutándose (PID $($Global:ProcBot.Id))" } else { "Detenido" }
    [System.Windows.Forms.MessageBox]::Show("Bot: " + $state + "`nLog: " + $Global:LogBot,"TGDL")
  }) | Out-Null
  $botItem.DropDown = $botMenu

  $panelMenu = New-Object System.Windows.Forms.ContextMenuStrip
  $panelMenu.Items.Add("Iniciar").add_Click({ Start-Panel })     | Out-Null
  $panelMenu.Items.Add("Reiniciar").add_Click({ Restart-Panel }) | Out-Null
  $panelMenu.Items.Add("Detener").add_Click({ Stop-Panel })      | Out-Null
  $panelMenu.Items.Add("Abrir en navegador").add_Click({ Start-Process $PanelUrl }) | Out-Null
  $panelMenu.Items.Add("Estado").add_Click({
    $state = if ($Global:ProcPanel -and -not $Global:ProcPanel.HasExited) { "Ejecutándose (PID $($Global:ProcPanel.Id))" } else { "Detenido" }
    [System.Windows.Forms.MessageBox]::Show("Panel: " + $state + "`nURL: " + $PanelUrl + "`nLog: " + $Global:LogPanel,"TGDL")
  }) | Out-Null
  $panelItem.DropDown = $panelMenu

  $aria2Menu = New-Object System.Windows.Forms.ContextMenuStrip
  $aria2Menu.Items.Add("Iniciar").add_Click({ Start-Aria2 })     | Out-Null
  $aria2Menu.Items.Add("Reiniciar").add_Click({ Restart-Aria2 }) | Out-Null
  $aria2Menu.Items.Add("Detener").add_Click({ Stop-Aria2 })      | Out-Null
  $aria2Menu.Items.Add("Estado").add_Click({
    $state = if ($Global:ProcAria2 -and -not $Global:ProcAria2.HasExited) { "Ejecutándose (PID $($Global:ProcAria2.Id))" } else { "Detenido" }
    [System.Windows.Forms.MessageBox]::Show("aria2: " + $state + "`nRPC: " + $ARIA2_JSONRPC + "`nLog: " + $Global:LogAria2,"TGDL")
  }) | Out-Null
  $aria2Item.DropDown = $aria2Menu

  $ni.ContextMenuStrip = $menu
  $ni.ShowBalloonTip(2000, "TGDL Manager", "Iniciando servicios…", [System.Windows.Forms.ToolTipIcon]::Info)

  # Arrancar todo
  Start-Aria2
  Start-Panel
  Start-Bot

  # Bucle de mensajes
  $null = New-Object System.Windows.Forms.Form
  [void][System.Windows.Forms.Application]::Run()
}
catch {
  # Cerrar transcript antes de escribir crash
  try { Stop-Transcript | Out-Null } catch {}
  try {
    $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $RepoRoot  = Resolve-Path (Join-Path $ScriptDir "..")
    $LogsDir   = Join-Path $RepoRoot "logs"
    New-Item -ItemType Directory -Force -Path $LogsDir | Out-Null
    $stamp2    = Get-Date -Format "yyyyMMdd-HHmmss"
    $CrashPath = Join-Path $LogsDir "tray-manager-crash-$stamp2.log"
    $msg = @(
      ("FATAL: " + $_.Exception.Message),
      "",
      "--- ScriptStackTrace ---",
      $_.ScriptStackTrace,
      "",
      "--- Exception ---",
      ($_.Exception | Out-String)
    ) -join "`r`n"
    Set-Content -LiteralPath $CrashPath -Value $msg -Encoding UTF8
  } catch {}
  throw
}
finally {
  try { Stop-Transcript | Out-Null } catch {}
}
