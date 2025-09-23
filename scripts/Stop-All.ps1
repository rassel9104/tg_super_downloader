Get-Process python, python3 | Where-Object { $_.Path -match "Python313|Python3" } | ForEach-Object {
    try {
        # Si deseas afinar, inspecciona $_.StartInfo.Arguments o CommandLine (requiere privilegios)
        Stop-Process -Id $_.Id -Force
    }
    catch {}
}
