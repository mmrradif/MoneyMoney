$desktop = [Environment]::GetFolderPath('Desktop')
$shortcutPath = Join-Path $desktop "MoneyMoney.lnk"
$wsh = New-Object -ComObject WScript.Shell
$shortcut = $wsh.CreateShortcut($shortcutPath)
$shortcut.TargetPath = "d:\MoneyMoney\Start_MoneyMoney.bat"
$shortcut.WorkingDirectory = "d:\MoneyMoney"
$shortcut.Description = "Launch MoneyMoney AI Bot and Web Dashboard"
$shortcut.Save()
Write-Host "Desktop shortcut created successfully at: $shortcutPath"
