@echo off
setlocal
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$taskName = 'KR DayPilot Daily Collect'; $programDir = (Resolve-Path -LiteralPath '.').Path; $bat = Join-Path $programDir 'run_daily_pipeline.bat'; Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue; $action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument ('/c \"' + $bat + '\"') -WorkingDirectory $programDir; $trigger = New-ScheduledTaskTrigger -Daily -At 15:45; $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2); $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive; Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'KR DayPilot daily candidate, intraday collection, and validation pipeline' | Out-Null"

echo.
echo Windows scheduled task registered: KR DayPilot Daily Collect
echo Run time: daily 15:45
pause
