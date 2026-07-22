param(
  [string]$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
  [string]$UserId = "all",
  [string]$UvPath = "uv",
  [bool]$RemoveLegacyTasks = $true
)

$UserFlag = "--all-users"
if ($UserId -ne "all") {
  $UserFlag = "--user $UserId"
}

if ($RemoveLegacyTasks) {
  @(
    "BUAA Zotero Arxiv Daily",
    "BUAA Zotero Arxiv Monthly"
  ) | ForEach-Object {
    if (Get-ScheduledTask -TaskName $_ -ErrorAction SilentlyContinue) {
      Unregister-ScheduledTask -TaskName $_ -Confirm:$false
      Write-Host "Removed legacy scheduled task: $_"
    }
  }
}

$dailyAction = New-ScheduledTaskAction `
  -Execute $UvPath `
  -Argument "run src/zotero_arxiv_daily/main.py $UserFlag --mode daily executor.max_paper_num=10" `
  -WorkingDirectory $ProjectDir
$dailyTrigger = New-ScheduledTaskTrigger -Daily -At 9:00AM
Register-ScheduledTask `
  -TaskName "zotero-arxiv-daily-$UserId-daily" `
  -Action $dailyAction `
  -Trigger $dailyTrigger `
  -Description "Daily arXiv + IACR ePrint paper push for $UserId" `
  -Force

$monthlyTaskName = "zotero-arxiv-daily-$UserId-monthly"
$monthlyArgs = "run src/zotero_arxiv_daily/main.py $UserFlag --mode monthly executor.max_paper_num=15"
$monthlyStart = (Get-Date -Hour 10 -Minute 0 -Second 0).ToString("yyyy-MM-ddTHH:mm:ss")
$escapedUvPath = [Security.SecurityElement]::Escape($UvPath)
$escapedMonthlyArgs = [Security.SecurityElement]::Escape($monthlyArgs)
$escapedProjectDir = [Security.SecurityElement]::Escape($ProjectDir)
$escapedMonthlyDescription = [Security.SecurityElement]::Escape("Monthly CCF Crossref/OpenAlex paper push for $UserId")
$monthlyTaskXml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>$escapedMonthlyDescription</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>$monthlyStart</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByMonth>
        <DaysOfMonth>
          <Day>1</Day>
        </DaysOfMonth>
        <Months>
          <January/><February/><March/><April/><May/><June/>
          <July/><August/><September/><October/><November/><December/>
        </Months>
      </ScheduleByMonth>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>$escapedUvPath</Command>
      <Arguments>$escapedMonthlyArgs</Arguments>
      <WorkingDirectory>$escapedProjectDir</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@
Register-ScheduledTask `
  -TaskName $monthlyTaskName `
  -Xml $monthlyTaskXml `
  -Force

Write-Host "Scheduled tasks installed for $UserId in $ProjectDir"
