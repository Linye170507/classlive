# ClassLive 一键启动（Windows PowerShell）
# 用法：powershell -ExecutionPolicy Bypass -File scripts\demo_windows.ps1
#      powershell -ExecutionPolicy Bypass -File scripts\demo_windows.ps1 -Simulators 20
param(
    [int]$Simulators = 0
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = "python"

Write-Host "ClassLive 工作目录：$root"

Start-Process -FilePath $python -ArgumentList "-m", "server.main" -WorkingDirectory $root
Start-Sleep -Milliseconds 800

Start-Process -FilePath $python -ArgumentList "-m", "clients.teacher_app" -WorkingDirectory $root -WindowStyle Hidden
Start-Sleep -Milliseconds 500

Start-Process -FilePath $python -ArgumentList "-m", "clients.student_app" -WorkingDirectory $root -WindowStyle Hidden

if ($Simulators -gt 0) {
    Start-Process -FilePath $python -ArgumentList "-m", "clients.simulator", "--count", "$Simulators", "--hold-s", "600" -WorkingDirectory $root -WindowStyle Hidden
}

Start-Sleep -Seconds 3
Write-Host "平铺窗口..."
& $python (Join-Path $PSScriptRoot "demo_layout.py")
Write-Host "完成。关闭：手动关闭各窗口，或 Stop-Process -Name python"
