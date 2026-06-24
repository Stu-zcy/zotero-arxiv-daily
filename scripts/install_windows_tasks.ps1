param(
  [string]$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
  [string]$UserId = "all",
  [string]$UvPath = "uv"
)

$UserFlag = "--all-users"
if ($UserId -ne "all") {
  $UserFlag = "--user $UserId"
}

$dailyAction = New-ScheduledTaskAction `
  -Execute $UvPath `
  -Argument "run src/zotero_arxiv_daily/main.py $UserFlag --mode daily --send-email true executor.max_paper_num=10" `
  -WorkingDirectory $ProjectDir
$dailyTrigger = New-ScheduledTaskTrigger -Daily -At 8:00AM
Register-ScheduledTask `
  -TaskName "zotero-arxiv-daily-$UserId-daily" `
  -Action $dailyAction `
  -Trigger $dailyTrigger `
  -Description "Daily arXiv + IACR ePrint paper push for $UserId" `
  -Force

$monthlyAction = New-ScheduledTaskAction `
  -Execute $UvPath `
  -Argument "run src/zotero_arxiv_daily/main.py $UserFlag --mode monthly executor.max_paper_num=15" `
  -WorkingDirectory $ProjectDir
$monthlyTrigger = New-ScheduledTaskTrigger -Monthly -DaysOfMonth 1 -At 9:00AM
Register-ScheduledTask `
  -TaskName "zotero-arxiv-daily-$UserId-monthly" `
  -Action $monthlyAction `
  -Trigger $monthlyTrigger `
  -Description "Monthly CCF Crossref/OpenAlex paper push for $UserId" `
  -Force

Write-Host "Scheduled tasks installed for $UserId in $ProjectDir"
