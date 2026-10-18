$ErrorActionPreference = 'Stop'
$here=Split-Path -Parent $MyInvocation.MyCommand.Path
$venv="$HOME\Downloads\FlowPromptAgent\.venv\Scripts\python.exe"
if(!(Test-Path $venv)){
  if(!(Test-Path "$here\.venv\Scripts\python.exe")){
    if(Get-Command py -ErrorAction SilentlyContinue){ & py -3.11 -m venv "$here\.venv" }
    elseif(Get-Command python -ErrorAction SilentlyContinue){ & python -m venv "$here\.venv" }
    else{ throw 'Install Python 3.11, then rerun.' }
  }
  $venv="$here\.venv\Scripts\python.exe"
}
& $venv -m pip install 'playwright>=1.48' 'pillow>=10' 'faster-whisper==1.2.1' 'av==18.1.0'
if($LASTEXITCODE -ne 0){throw 'Python dependency installation failed.'}
foreach($cmd in @('ffmpeg','ffprobe')){
  if(!(Get-Command $cmd -ErrorAction SilentlyContinue)){ throw "$cmd is not in PATH. Install FFmpeg first." }
}
Write-Host 'Installed. Keep signed-in ChatGPT, Google AI Studio and Google Flow accessible in Chrome port 9333.'
