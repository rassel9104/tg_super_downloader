Option Explicit

Dim fso, sh, base, ps64, cmd, logdir, log
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")

base   = fso.GetParentFolderName(WScript.ScriptFullName)
logdir = fso.BuildPath(fso.GetParentFolderName(base), "logs")
If Not fso.FolderExists(logdir) Then fso.CreateFolder(logdir)
log = fso.BuildPath(logdir, "tray-launch.log")

' Forzar PowerShell 64-bit (cuando el host sea wscript de 32-bit)
ps64 = sh.ExpandEnvironmentStrings("%SystemRoot%\Sysnative\WindowsPowerShell\v1.0\powershell.exe")
If Not fso.FileExists(ps64) Then
  ps64 = sh.ExpandEnvironmentStrings("%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe")
End If

' Ejecutar oculto, STA, sin perfil, y redirigir stdout/err a log
cmd = "cmd.exe /c """ & ps64 & " -NoLogo -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -sta -File """ & base & "\Tray-Manager.ps1"" >> """ & log & """ 2>&1"""

' 0 = oculto, False = no esperar
sh.Run cmd, 0, False
