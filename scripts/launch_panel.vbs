Set sh = CreateObject("WScript.Shell")
cmd = "powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & WScript.ScriptFullName & "\..\Start-Panel.ps1"""
sh.Run cmd, 0, False
