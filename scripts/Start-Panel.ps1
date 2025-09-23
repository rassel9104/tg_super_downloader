#Requires -Version 5.1
Add-Type -AssemblyName System.Windows.Forms | Out-Null
Add-Type -AssemblyName System.Drawing        | Out-Null

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..")
$LogsDir = Join-Path $RepoRoot "logs"
$IconPath = Join-Path $ScriptDir "icons\panel.ico"

New-Item -ItemType Directory -Force -Path $LogsDir | Out-Null
Set-Location $RepoRoot

# Cargar configuración para abrir URL del panel (sanea comillas y espacios)
function Sanitize([string]$val) {
    if (-not $val) { return $null }
    $v = $val.Trim()
    # quita comillas simples o dobles envolventes
    return ($v -replace '^\s*["'']|["'']\s*$', '')
}

try {
    $envFile = Join-Path $RepoRoot ".env"
    if (Test-Path $envFile) {
        Get-Content $envFile | ForEach-Object {
            if ($_ -match '^\s*PANEL_HOST\s*=\s*(.+)$') { $env:PANEL_HOST = Sanitize $Matches[1] }
            if ($_ -match '^\s*PANEL_PORT\s*=\s*(.+)$') { $env:PANEL_PORT = Sanitize $Matches[1] }
        }
    }
}
catch {}

if (-not $env:PANEL_HOST) { $env:PANEL_HOST = "127.0.0.1" }
if (-not $env:PANEL_PORT) { $env:PANEL_PORT = "8080" }

# Construir URL (maneja IPv6 con corchetes)
$host1 = $env:PANEL_HOST
if ($host1 -match ":") { $host1 = "[" + $host1.Trim("[]") + "]" }
$PanelUrl = "http://$host1:$($env:PANEL_PORT)/"


# Python
$PyLocal = Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"
$Python = if (Test-Path $PyLocal) { $PyLocal } else { "python" }

function Start-PanelProcess {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $Global:PanelLog = Join-Path $LogsDir "panel-$stamp.log"

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $Python
    $psi.Arguments = "-m tgdl.cli panel"
    $psi.WorkingDirectory = $RepoRoot
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true

    $Global:PanelProc = New-Object System.Diagnostics.Process
    $Global:PanelProc.StartInfo = $psi

    $null = Register-ObjectEvent -InputObject $Global:PanelProc -EventName "OutputDataReceived" -SourceIdentifier "panel_out" -Action {
        if ($EventArgs.Data) { Add-Content -Path $Global:PanelLog -Value $EventArgs.Data }
    }
    $null = Register-ObjectEvent -InputObject $Global:PanelProc -EventName "ErrorDataReceived"  -SourceIdentifier "panel_err" -Action {
        if ($EventArgs.Data) { Add-Content -Path $Global:PanelLog -Value $EventArgs.Data }
    }

    [void]$Global:PanelProc.Start()
    $Global:PanelProc.BeginOutputReadLine()
    $Global:PanelProc.BeginErrorReadLine()
}

function Stop-PanelProcess {
    if ($Global:PanelProc -and -not $Global:PanelProc.HasExited) {
        try { $Global:PanelProc.Kill() } catch {}
        Start-Sleep -Milliseconds 200
    }
    Get-EventSubscriber | Where-Object { $_.SourceIdentifier -in @("panel_out", "panel_err") } | Unregister-Event
}

# Notify icon
$ni = New-Object System.Windows.Forms.NotifyIcon
if (Test-Path $IconPath) {
    $ni.Icon = [System.Drawing.Icon]::ExtractAssociatedIcon($IconPath)
}
else {
    $ni.Icon = [System.Drawing.Icon]::ExtractAssociatedIcon("$PSHOME\powershell.exe")
}
$ni.Visible = $true
$ni.Text = "TGDL Panel"

$menu = New-Object System.Windows.Forms.ContextMenuStrip
$miOpen = $menu.Items.Add("Abrir panel")
$miLogs = $menu.Items.Add("Abrir carpeta de logs")
$miRestart = $menu.Items.Add("Reiniciar panel")
$miStop = $menu.Items.Add("Detener y salir")

$miOpen.add_Click({ Start-Process $PanelUrl })
$miLogs.add_Click({ if (Test-Path $LogsDir) { Invoke-Item $LogsDir } })
$miRestart.add_Click({
        Stop-PanelProcess
        Start-PanelProcess
        $ni.ShowBalloonTip(2000, "TGDL Panel", "Reiniciado", [System.Windows.Forms.ToolTipIcon]::Info)
    })
$miStop.add_Click({
        Stop-PanelProcess
        $ni.Visible = $false
        $ni.Dispose()
        Stop-Process -Id $PID -Force
    })

$ni.ContextMenuStrip = $menu
$ni.ShowBalloonTip(2000, "TGDL Panel", "Iniciando…", [System.Windows.Forms.ToolTipIcon]::Info)

Start-PanelProcess

$null = New-Object System.Windows.Forms.Form
[void][System.Windows.Forms.Application]::Run()
