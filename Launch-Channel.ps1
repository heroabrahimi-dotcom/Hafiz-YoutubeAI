param([ValidateSet('ancient','survival')][string]$Mode='ancient',[switch]$FreshStart)
$ErrorActionPreference='Stop'
$here=Split-Path -Parent $MyInvocation.MyCommand.Path
try { $null=Invoke-RestMethod -Uri 'http://127.0.0.1:9333/json/version' -TimeoutSec 3 }
catch {
  $paths=@("$env:ProgramFiles\Google\Chrome\Application\chrome.exe", "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe", "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe")
  $chrome=$paths | Where-Object { Test-Path $_ } | Select-Object -First 1
  if(-not $chrome) { throw 'Chrome not found; install Google Chrome.' }
  $profile="$env:LOCALAPPDATA\ChromeAutomationProfile2"
  Start-Process -FilePath $chrome -ArgumentList @('--remote-debugging-port=9333',"--user-data-dir=`"$profile`"",'https://chatgpt.com','https://flow.google.com') | Out-Null
  $ready=$false
  for($i=0;$i -lt 50;$i++) { Start-Sleep -Milliseconds 500; try {$null=Invoke-RestMethod -Uri 'http://127.0.0.1:9333/json/version' -TimeoutSec 2; $ready=$true; break} catch {} }
  if(-not $ready){throw 'Chrome port 9333 not ready. Close other automation Chrome windows and try again.'}
}
& "$here\Start-AutoVideo.ps1" -Mode $Mode -FreshStart:$FreshStart
if($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
