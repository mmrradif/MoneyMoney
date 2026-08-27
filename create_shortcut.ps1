$desktop = [Environment]::GetFolderPath('Desktop')
$shortcutPath = Join-Path $desktop "Money Maker.lnk"
$wsh = New-Object -ComObject WScript.Shell
$shortcut = $wsh.CreateShortcut($shortcutPath)
$shortcut.TargetPath = "d:\Money Maker\Start_Money_Maker.bat"
$shortcut.WorkingDirectory = "d:\Money Maker"
$shortcut.Description = "Launch Money Maker AI Bot and Web Dashboard"
$shortcut.Save()
Write-Host "Desktop shortcut created successfully at: $shortcutPath"
