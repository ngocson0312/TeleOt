<#
Dừng bot đang chạy nền trên Windows (tác vụ TeleOt và tiến trình python của nó).
Bật lại: Start-ScheduledTask -TaskName TeleOt  (hoặc khởi động lại máy).
Cần PowerShell "Run as administrator" vì bot chạy bằng tài khoản SYSTEM.
#>
param([string]$TaskName = "TeleOt")
$root = Split-Path -Parent $PSScriptRoot

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
$wasRunning = $task -and $task.State -eq "Running"
# Stopping the task usually ends the bot too; the search below catches anything left behind.
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

# .venv\Scripts\python.exe starts the real interpreter as a child process: stop both.
$procs = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'")
$launchers = @($procs | Where-Object { $_.ExecutablePath -like "$root\*" -and $_.CommandLine -like "*app.main*" })
$ids = @($launchers | ForEach-Object { $_.ProcessId })
$ids += @($procs | Where-Object { $ids -contains $_.ParentProcessId } | ForEach-Object { $_.ProcessId })
foreach ($id in $ids) {
    Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
}
if ($wasRunning -or $ids.Count -gt 0) { Write-Host "Đã dừng bot." } else { Write-Host "Bot không chạy." }
