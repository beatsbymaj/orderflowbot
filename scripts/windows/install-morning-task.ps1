# One-time: register the weekday 09:20 FlowForge morning task (run in an admin PowerShell).
# Remove it again with: Unregister-ScheduledTask -TaskName "FlowForge morning" -Confirm:$false
$script = (Resolve-Path "$PSScriptRoot\flowforge-morning.ps1").Path
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`""
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At 9:20am
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 3)
Register-ScheduledTask -TaskName "FlowForge morning" -Action $action -Trigger $trigger -Settings $settings `
    -User $env:USERNAME -RunLevel Limited -Force | Out-Null
Write-Output "Registered 'FlowForge morning' (weekdays 09:20, local time; the PC must be set to Eastern time)."
