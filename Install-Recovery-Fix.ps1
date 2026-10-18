$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$pipeline = Join-Path $here 'pipeline.py'
$old = [System.IO.File]::ReadAllText($pipeline)
$needle = "        if p.data.get('tts_uncertain')==part:`n            raise UIBlocked(f'Part {part} may have already been generated. Download it from AI Studio into {audio_dir} as part_{part}.wav (or .mp3), then rerun. To deliberately retry, pass --retry-voice-part {part}.')"
$needle = $needle.Replace("`n", "`n")
$replacement = "        if p.data.get('tts_uncertain')==part:`n            from recover_existing_audio import recover_existing_audio`n            recovered = recover_existing_audio(context,p,part)`n            verify_audio_length(p,part,recovered)`n            p.data['tts_uncertain']=None;p.set(f'audio{part}')`n            audio_files.append(recovered)`n            continue"
if ($old.Contains('from recover_existing_audio import recover_existing_audio')) {
    Write-Host 'Recovery fix already installed.'
} else {
    $normalized = $old.Replace("`r`n", "`n")
    if (-not $normalized.Contains($needle)) { throw 'Cannot locate expected pending-audio block in pipeline.py; no changes made.' }
    Copy-Item $pipeline "$pipeline.before_audio_recovery.bak" -Force
    $patched = $normalized.Replace($needle,$replacement)
    [System.IO.File]::WriteAllText($pipeline,$patched,[System.Text.UTF8Encoding]::new($false))
    Write-Host 'Patched pipeline.py (original backed up).'
}
$py = "$HOME\Downloads\FlowPromptAgent\.venv\Scripts\python.exe"
if (Test-Path $py) {
    & $py -m py_compile "$here\recover_existing_audio.py" $pipeline
    if ($LASTEXITCODE -ne 0) {throw 'Python syntax check failed.'}
}
Write-Host 'Recovery ready. Run .\Start-AutoVideo.ps1 without FreshStart or RetryVoicePart.'
