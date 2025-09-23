#Requires -Version 5.1
Add-Type -AssemblyName System.Windows.Forms | Out-Null
Add-Type -AssemblyName System.Drawing        | Out-Null

# --- Paths ---
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..")
$LogsDir = Join-Path $RepoRoot "logs"
$IconPath = Join-Path $ScriptDir "icons\bot.ico"  # opcional; si no existe, se usa fallback

New-Item -ItemType Directory -Force -Path $LogsDir | Out-Null
Set-Location $RepoRoot

# --- Python selector ---
$PyLocal = Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"
$Python = if (Test-Path $PyLocal) { $PyLocal } else { "python" }

# --- Proc launcher ---
function Start-BotProcess {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $Global:BotLog = Join-Path $LogsDir "bot-$stamp.log"

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $Python
    $psi.Arguments = "-m tgdl.cli bot"
    $psi.WorkingDirectory = $RepoRoot
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true

    $Global:BotProc = New-Object System.Diagnostics.Process
    $Global:BotProc.StartInfo = $psi

    # Handlers de salida -> log
    $null = Register-ObjectEvent -InputObject $Global:BotProc -EventName "OutputDataReceived" -SourceIdentifier "bot_out" -Action {
        if ($EventArgs.Data) { Add-Content -Path $Global:BotLog -Value $EventArgs.Data }
    }
    $null = Register-ObjectEvent -InputObject $Global:BotProc -EventName "ErrorDataReceived"  -SourceIdentifier "bot_err" -Action {
        if ($EventArgs.Data) { Add-Content -Path $Global:BotLog -Value $EventArgs.Data }
    }

    [void]$Global:BotProc.Start()
    $Global:BotProc.BeginOutputReadLine()
    $Global:BotProc.BeginErrorReadLine()
}

function Stop-BotProcess {
    if ($Global:BotProc -and -not $Global:BotProc.HasExited) {
        try { $Global:BotProc.Kill() } catch {}
        Start-Sleep -Milliseconds 200
    }
    Get-EventSubscriber | Where-Object { $_.SourceIdentifier -in @("bot_out", "bot_err") } | Unregister-Event
}

# --- Notify icon ---
$ni = New-Object System.Windows.Forms.NotifyIcon
if (Test-Path $IconPath) {
    $ni.Icon = [System.Drawing.Icon]::ExtractAssociatedIcon($IconPath)
}
else {
    $ni.Icon = [System.Drawing.Icon]::ExtractAssociatedIcon("$PSHOME\powershell.exe")
}
$ni.Visible = $true
$ni.Text = "TGDL Bot"

# --- Menú contextual ---
$menu = New-Object System.Windows.Forms.ContextMenuStrip

$miStatus = $menu.Items.Add("Estado")
$miLogs = $menu.Items.Add("Abrir carpeta de logs")
$miRestart = $menu.Items.Add("Reiniciar bot")
$miStop = $menu.Items.Add("Detener y salir")

$miStatus.add_Click({
        $state = if ($Global:BotProc -and -not $Global:BotProc.HasExited) { "En ejecución (PID $($Global:BotProc.Id))" } else { "Detenido" }
        [System.Windows.Forms.MessageBox]::Show("Bot: $state`nLog: $Global:BotLog", "TGDL Bot")
    })
$miLogs.add_Click({ if (Test-Path $LogsDir) { Invoke-Item $LogsDir } })
$miRestart.add_Click({
        Stop-BotProcess
        Start-BotProcess
        $ni.ShowBalloonTip(2000, "TGDL Bot", "Reiniciado", [System.Windows.Forms.ToolTipIcon]::Info)
    })
$miStop.add_Click({
        Stop-BotProcess
        $ni.Visible = $false
        $ni.Dispose()
        Stop-Process -Id $PID -Force
    })

$ni.ContextMenuStrip = $menu

# Tooltip al iniciar
$ni.ShowBalloonTip(2000, "TGDL Bot", "Iniciando…", [System.Windows.Forms.ToolTipIcon]::Info)

# Lanzar bot
Start-BotProcess

# Mantener el icono vivo (bucle de mensajes WinForms)
$null = New-Object System.Windows.Forms.Form  # dummy para message loop
[void][System.Windows.Forms.Application]::Run()
