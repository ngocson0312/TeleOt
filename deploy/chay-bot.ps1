<#
Chạy bot và tự chạy lại sau 30 giây nếu bot dừng.
Task Scheduler gọi file này khi máy khởi động (cài bằng deploy\cai-dat-windows.ps1).

Log của bot:   data\logs\teleot.log  (mỗi ngày một file, giữ 14 ngày)
Lần bật/tắt:   data\logs\chay-bot.log
Lỗi khởi động: data\logs\loi-khoi-dong.txt  (ví dụ thiếu token, sai SECRET_KEY)
#>
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$logDir = Join-Path $root "data\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$runLog = Join-Path $logDir "chay-bot.log"
$env:LOG_FILE = Join-Path $logDir "teleot.log"
$env:PYTHONUNBUFFERED = "1"
$env:PYTHONIOENCODING = "utf-8"

function Write-RunLog([string]$text) {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $text
    Add-Content -Path $runLog -Value $line -Encoding UTF8
}

while ($true) {
    Write-RunLog "Bắt đầu bot"
    $startArgs = @{
        FilePath = $python
        ArgumentList = @("-m", "app.main")
        WorkingDirectory = $root
        RedirectStandardError = (Join-Path $logDir "loi-khoi-dong.txt")
        NoNewWindow = $true
        PassThru = $true
        Wait = $true
    }
    $p = Start-Process @startArgs
    # 3 = another copy already runs on this machine: check again later, quietly
    $wait = if ($p.ExitCode -eq 3) { 300 } else { 30 }
    Write-RunLog ("Bot dừng (mã {0}), chạy lại sau {1} giây" -f $p.ExitCode, $wait)
    Start-Sleep -Seconds $wait
}
