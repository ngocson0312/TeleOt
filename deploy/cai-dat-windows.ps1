<#
Cài bot chạy 24/7 trên máy Windows bật suốt ngày đêm.
Task Scheduler bật bot khi máy khởi động (không cần đăng nhập) và bot dừng thì tự chạy lại.

Mở PowerShell bằng "Run as administrator", rồi:
    cd D:\____TTT_____\TeleOT
    powershell -ExecutionPolicy Bypass -File deploy\cai-dat-windows.ps1

Gỡ:  powershell -ExecutionPolicy Bypass -File deploy\cai-dat-windows.ps1 -Uninstall
Chạy lại script nhiều lần không sao (ví dụ sau khi git pull để cài thư viện mới).
#>
param([switch]$Uninstall, [string]$TaskName = "TeleOt")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$runner = Join-Path $PSScriptRoot "chay-bot.ps1"
$envFile = Join-Path $root ".env"

$identity = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $identity.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Mở PowerShell bằng 'Run as administrator' rồi chạy lại."
    exit 1
}

if ($Uninstall) {
    & (Join-Path $PSScriptRoot "dung-bot.ps1") -TaskName $TaskName
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Đã gỡ tác vụ $TaskName. Dữ liệu trong data\ vẫn còn nguyên."
    exit 0
}

Set-Location $root
if (-not (Test-Path $python)) {
    Write-Host "==> Tạo môi trường Python (.venv)"
    if (Get-Command py -ErrorAction SilentlyContinue) { & py -3 -m venv .venv } else { & python -m venv .venv }
    if (-not (Test-Path $python)) { throw "Không tạo được .venv. Cài Python 3.12+ từ python.org trước." }
}

Write-Host "==> Thư viện Python"
& $python -m pip install -q --disable-pip-version-check -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Cài thư viện lỗi (pip install)." }

if (-not (Test-Path $envFile)) {
    Write-Host "==> Tạo file .env và khóa mã hóa SECRET_KEY"
    $key = (& $python -m app.cli gen-key | Select-Object -Last 1).Trim()
    $text = [IO.File]::ReadAllText((Join-Path $root ".env.example"))
    $text = $text -replace '(?m)^SECRET_KEY=[^\r\n]*', "SECRET_KEY=$key"
    [IO.File]::WriteAllText($envFile, $text, (New-Object Text.UTF8Encoding $false))
}

Write-Host "==> Tác vụ Task Scheduler: $TaskName"
$taskArgs = @{
    TaskName = $TaskName
    Description = "TeleOt: bot báo cáo KiotViet -> Telegram. Log: $root\data\logs"
    Action = (New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $root `
        -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $runner))
    Trigger = (New-ScheduledTaskTrigger -AtStartup)
    Principal = (New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest)
    # ExecutionTimeLimit 0 = no limit (the default would stop the bot after 3 days)
    Settings = (New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1))
    Force = $true
}
Register-ScheduledTask @taskArgs | Out-Null

Write-Host ""
if (Select-String -Path $envFile -Pattern '^TELEGRAM_BOT_TOKEN=.+' -Quiet) {
    & (Join-Path $PSScriptRoot "dung-bot.ps1") -TaskName $TaskName | Out-Null
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Bot đã chạy nền và sẽ tự bật khi máy khởi động lại."
    Write-Host "  Xem log:  Get-Content data\logs\teleot.log -Tail 50 -Wait -Encoding UTF8"
    Write-Host "  Dừng:     powershell -ExecutionPolicy Bypass -File deploy\dung-bot.ps1"
} else {
    Write-Host "Đã cài xong. Còn mấy bước:"
    Write-Host "  1. notepad .env   (điền TELEGRAM_BOT_TOKEN, ADMIN_CHAT_ID)"
    Write-Host "  2. .venv\Scripts\python -m app.cli tenant-add --name `"Tên NPP`""
    Write-Host "  3. .venv\Scripts\python -m app.cli sync 1"
    Write-Host "  4. Start-ScheduledTask -TaskName $TaskName"
    Write-Host "  Hướng dẫn đầy đủ: CAI-DAT.md"
}
Write-Host "Nhớ chỉnh máy không tự ngủ (Settings > System > Power), ngủ là bot dừng."
