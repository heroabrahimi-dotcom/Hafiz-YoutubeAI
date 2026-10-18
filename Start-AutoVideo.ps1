param(
  [ValidateSet('ancient','survival')][string]$Mode = 'ancient',
  [switch]$DryRun,
  [ValidateSet('private','unlisted','public')][string]$YouTubeVisibility = 'private',
  [switch]$FreshStart,
  [string]$Guide = "$HOME\Downloads\Prompts.pdf",
  [int]$Videos = 1,
  [int]$MaxBatches = 0,
  [ValidateSet('scripts','audio','storyboard','images','')][string]$StopAfter = '',
  [ValidateSet(0,1,2)][int]$RetryVoicePart = 0,
  [ValidateSet('tiny','base','small','medium')][string]$WhisperModel = 'small'
)
$ErrorActionPreference='Stop'
$here=Split-Path -Parent $MyInvocation.MyCommand.Path
$possible=@("$HOME\Downloads\FlowPromptAgent\.venv\Scripts\python.exe", "$here\.venv\Scripts\python.exe")
$python=$possible | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $python) { throw 'Python environment missing. Run .\Install.ps1 first.' }
$env:PYTHONUNBUFFERED='1'
$env:YOUTUBE_MODE=$Mode
if($Mode -eq 'survival') {
  $env:YOUTUBE_VOICE_STYLE='Tense, immersive survival thriller narration. Use the Enzo voice with natural conversational delivery at approximately 1.15x pace. Build suspense through subtle emotional variation and short deliberate pauses before twists. Increase urgency during danger, but never shout, rush words, or overact. Maintain clear pronunciation and calm breathing between intense moments.'
  $env:SFX_VOLUME='0.25'
  $env:YOUTUBE_VISUAL_PACING='dynamic'
  $env:YOUTUBE_STORY_PERSPECTIVE='second'
  $env:YOUTUBE_MUSIC_STYLE='subtle'
  $env:YOUTUBE_MUSIC_DIR='C:\YouTubeAI\Music\Survival'

  $env:YOUTUBE_PROJECT_ROOT='C:\YouTubeAI\Projects\Survival'
  if ($Guide -eq "$HOME\Downloads\Prompts.pdf") { $Guide="$here\Prompts-survival.pdf" }
} else {
  foreach($name in @('YOUTUBE_VOICE_STYLE','SFX_VOLUME','YOUTUBE_VISUAL_PACING','YOUTUBE_STORY_PERSPECTIVE','YOUTUBE_MUSIC_STYLE','YOUTUBE_MUSIC_DIR')) { Remove-Item "Env:$name" -ErrorAction SilentlyContinue }
  $env:YOUTUBE_PROJECT_ROOT='C:\YouTubeAI\Projects'
  if ($Guide -eq "$HOME\Downloads\Prompts.pdf") { $Guide="$here\Prompts-ancient.pdf" }
}
Write-Host "MODE: $Mode | GUIDE: $Guide | PROJECT ROOT: $env:YOUTUBE_PROJECT_ROOT"

$argsList=@('-u', "$here\pipeline.py", '--python', $python, '--videos', "$Videos", '--max-batches', "$MaxBatches", '--whisper-model', $WhisperModel, '--youtube-visibility', $YouTubeVisibility)
if($DryRun){ $argsList+='--dry-run' }
if($FreshStart){ $argsList+='--fresh-start' }
$argsList+=@('--guide',$Guide)
if($StopAfter){ $argsList+=@('--stop-after',$StopAfter) }
if($RetryVoicePart){ $argsList+=@('--retry-voice-part',"$RetryVoicePart") }
& $python @argsList
if($LASTEXITCODE -ne 0){ throw "AutoVideo stopped with code $LASTEXITCODE. See the error above. All confirmed progress is saved." }
