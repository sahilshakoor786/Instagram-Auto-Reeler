# Registers a Windows Task Scheduler job that runs `python -m reeler post` every 15 minutes.
# It only uploads when one of the post_times in config.yaml is due, so the PC can sleep in between.
# Run from PowerShell inside the project folder:  powershell -ExecutionPolicy Bypass -File scripts\install_task_windows.ps1
$project = Resolve-Path "$PSScriptRoot\.."
$python  = if (Test-Path "$project\.venv\Scripts\python.exe") { "$project\.venv\Scripts\python.exe" } else { (Get-Command python).Source }
$action  = New-ScheduledTaskAction -Execute $python -Argument "-m reeler post" -WorkingDirectory $project
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 15)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries
Register-ScheduledTask -TaskName "InstagramAutoReeler" -Action $action -Trigger $trigger -Settings $settings -Force
Write-Host "Scheduled. Remove with: Unregister-ScheduledTask -TaskName InstagramAutoReeler"
