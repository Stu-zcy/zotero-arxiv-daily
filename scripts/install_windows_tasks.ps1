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

$monthlyTaskName = "zotero-arxiv-daily-$UserId-monthly"
$monthlyCommand = "cd /d `"$ProjectDir`" && `"$UvPath`" run src/zotero_arxiv_daily/main.py $UserFlag --mode monthly executor.max_paper_num=15"
schtasks.exe /Create `
  /TN $monthlyTaskName `
  /SC MONTHLY `
  /D 1 `
  /ST 09:00 `
  /TR "cmd.exe /c $monthlyCommand" `
  /F | Out-Host

Write-Host "Scheduled tasks installed for $UserId in $ProjectDir"
