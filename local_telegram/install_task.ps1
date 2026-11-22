[CmdletBinding()]
param([switch]$Install)

$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -eq 'true') { throw 'This task is local only.' }
$root = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $root '.venv\Scripts'
$python = Join-Path $venv 'python.exe'
$pythonw = Join-Path $venv 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) {
    throw 'Run TELEGRAM_LOGIN.bat first to prepare the local Python environment.'
}

$taskName = 'Crypto Cash Hunter - Telegram'
$description = 'Crypto Cash Hunter local Telegram folder monitor (managed by install_task.ps1)'
$hours = 0, 6, 12, 18
$who = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$triggers = foreach ($hour in $hours) {
    New-ScheduledTaskTrigger -Daily -At ((Get-Date).Date.AddHours($hour).AddMinutes(17))
}
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId $who -LogonType Interactive -RunLevel Limited

if (-not $Install) {
    Write-Output 'Validation only. Use -Install after approving the local schedule.'
    return
}

$existing = Get-ScheduledTask -TaskName $taskName -TaskPath '\' -ErrorAction SilentlyContinue
if ($existing -and $existing.Description -ne $description) {
    throw 'A different task already has this name.'
}

$commit = git -C $root rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $commit -notmatch '^[0-9a-f]{40}$') { throw 'Cannot resolve Git revision.' }
$localDir = Join-Path $env:LOCALAPPDATA 'CryptoIntelligence\runner'
$runtime = Join-Path $localDir ($commit.Substring(0,12) + '-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $runtime
$zip = Join-Path $runtime 'code.zip'
try {
    git -C $root archive --format=zip -o $zip $commit -- agent.py watchlist.json intelligence local_telegram
    if ($LASTEXITCODE -ne 0) { throw 'Could not export committed code.' }
    Expand-Archive -LiteralPath $zip -DestinationPath $runtime
    Push-Location -LiteralPath $runtime
    try {
        & $python -B -c 'import telethon, win32crypt; from local_telegram import windows_monitor'
        if ($LASTEXITCODE -ne 0) { throw 'The exported monitor or its dependencies are missing.' }
    } finally {
        Pop-Location
    }
} finally {
    Remove-Item -LiteralPath $zip -ErrorAction SilentlyContinue
}
$action = New-ScheduledTaskAction -Execute $pythonw -WorkingDirectory $runtime `
    -Argument '-B -m local_telegram.windows_monitor'
Register-ScheduledTask -TaskName $taskName -TaskPath '\' -Description $description -Action $action `
    -Trigger $triggers -Settings $settings -Principal $principal -Force:([bool]$existing)
