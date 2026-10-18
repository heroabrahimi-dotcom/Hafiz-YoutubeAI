"""Safe recovery for a prior TTS submission: never clicks Run.
Only downloads from an AI Studio page that still contains this project's script.
"""
import time
from pathlib import Path
from browser_ops import log, snapshot, studio_wait_for_audio, UIBlocked


def recover_existing_audio(context, project, part):
    script = project.path(f'script/part_{part}.txt').read_text(encoding='utf-8').strip()
    audio_dir = project.path('audio')
    diagnostics = project.path('diagnostics')
    snippets = [' '.join(script.split())[:100]]
    log(f'Part {part}: previous Run was submitted. Checking open AI Studio tabs for its result; will NOT regenerate.')
    for page in context.pages:
        if 'aistudio.google.com' not in page.url or 'generate-speech' not in page.url:
            continue
        try:
            # Read the real contenteditable/textarea values, not unrelated page body labels.
            editor_texts = page.locator('textarea,[contenteditable="true"], [role="textbox"]').evaluate_all(
                '''els => els.map(e => (e.value || e.innerText || e.textContent || '').replace(/\\s+/g,' ').trim())''')
            short = snippets[0]
            if not any(short[:65] in s for s in editor_texts):
                log('AI Studio tab found, but it does not contain this project\'s narration. Skipping it.')
                continue
            log(f'Found matching Part {part} in open AI Studio tab. Waiting for its existing download; no new Run click.')
            # Don't close this page: the submitted job may still be in progress.
            return studio_wait_for_audio(page, audio_dir / f'part_{part}.wav', diagnostics, part, max_wait_seconds=2700)
        except UIBlocked:
            raise
        except Exception as exc:
            log(f'Could not inspect one AI Studio tab safely: {str(exc)[:150]}')
    raise UIBlocked(
        f'Part {part} was previously submitted, but its original speech editor is no longer open with the script. '
        'The older script closed its AI Studio tab after a download error, so there is nothing live to wait on. '
        'Open the generated result from AI Studio History and keep that editor tab open, then rerun. '
        'This recovery will NOT click Run or create duplicate audio.')
