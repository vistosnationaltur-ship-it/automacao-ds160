$ws = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath('Desktop')
$pasta = $PSScriptRoot

$s1 = $ws.CreateShortcut("$desktop\1 - Extrair PDF do Cliente.lnk")
$s1.TargetPath = "$pasta\1 - Extrair PDF do Cliente.bat"
$s1.WorkingDirectory = $pasta
$s1.IconLocation = "shell32.dll,1"
$s1.Save()

$s2 = $ws.CreateShortcut("$desktop\2 - Preencher DS160 (Robo).lnk")
$s2.TargetPath = "$pasta\2 - Preencher DS160 (Robo).bat"
$s2.WorkingDirectory = $pasta
$s2.IconLocation = "shell32.dll,13"
$s2.Save()

Write-Host "Atalhos criados na Area de Trabalho."
