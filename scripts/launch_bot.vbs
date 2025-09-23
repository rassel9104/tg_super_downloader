Set sh = CreateObject("WScript.Shell")
' Ejecuta PowerShell oculto (-WindowStyle Hidden)
cmd = "powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & WScript.ScriptFullName & "\..\Start-Bot.ps1"""
sh.Run cmd, 0, False
