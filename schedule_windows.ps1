# Registers an hourly Windows Task Scheduler job that runs the collector inside WSL.
# UNTESTED here (no Windows in my sandbox). Run in PowerShell; adjust the distro name and project path.
$Distro  = "Ubuntu"
$Project = "~/boxoffice_tracker"
$action   = New-ScheduledTaskAction -Execute "wsl.exe" -Argument "-d $Distro -e bash -lc `"cd $Project && ./run_hourly.sh`""
$start    = (Get-Date).Date.AddHours((Get-Date).Hour + 1).AddMinutes(7)   # :07 past the hour, off the busy :00 mark
$trigger  = New-ScheduledTaskTrigger -Once -At $start -RepetitionInterval (New-TimeSpan -Hours 1)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
Register-ScheduledTask -TaskName "BoxOfficeHourly" -Action $action -Trigger $trigger -Settings $settings -Description "Hourly box-office snapshot"
# Remove later with: Unregister-ScheduledTask -TaskName BoxOfficeHourly -Confirm:$false
