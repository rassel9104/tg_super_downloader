Start-Process -WindowStyle Hidden -FilePath "powershell" -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"%~dp0Start-Bot.ps1`""
Start-Process -WindowStyle Hidden -FilePath "powershell" -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"%~dp0Start-Panel.ps1`""
