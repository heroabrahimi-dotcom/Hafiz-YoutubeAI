param()

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Py = Join-Path $HOME "Downloads\FlowPromptAgent\.venv\Scripts\python.exe"
$Watch = Join-Path $Root "auto_2k_watch.py"
$Projects = "C:\YouTubeAI\Projects\Survival"

$Existing = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -like "*auto_2k_watch.py*" -and $_.CommandLine -like "*C:\YouTubeAI\Projects\Survival*"
}

if (-not $Existing) {
    Start-Process -WindowStyle Hidden -FilePath $Py -ArgumentList @($Watch, "--projects-root", $Projects)
    Start-Sleep -Seconds 2
    Write-Host "AUTO2K watcher started." -ForegroundColor Green
} else {
    Write-Host "AUTO2K watcher already running." -ForegroundColor Yellow
}

& (Join-Path $Root "Start-AutoVideo.ps1") -Mode survival
