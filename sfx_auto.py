"""Local, script-aware SFX pass for AncientYouTubeAuto.

One effect MAX in each consecutive 3-visual group AND at least 3 visual
numbers between effect starts. No remote API or YouTube requests.
Safe on restart: always mixes from a verified original-video backup.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

FORMATS = {'.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg', '.opus', '.webm'}
SKIP_NAME = {
    'sfx', 'sound', 'sounds', 'effect', 'effects', 'audio', 'free', 'copyright',
    'royalty', 'youtube', 'download', 'track', 'sample', 'clip', 'version',
    'short', 'long', 'high', 'low', 'quality', 'original', 'official',
    'no', 'the', 'a', 'an', 'and', 'of', 'with', 'for', 'to', 'on', 'in',
    'recording', 'field', 'library', 'loop', '1', '2', '3', '4', '5',
}

# Both filename and script are matched via categories to cover phrasing variants.
EVENTS = {
    'bell': ('bell', 'bells', 'chime', 'chimes', 'ringing', 'ring', 'toll'),
    'knock': ('knock', 'knocking', 'knocks', 'tap', 'tapping', 'pound', 'pounding'),
    'door': ('door', 'doors', 'gate', 'entrance', 'latch', 'hinge'),
    'creak': ('creak', 'creaking', 'squeak', 'squeaking'),
    'footsteps': ('footstep', 'footsteps', 'steps', 'walking', 'walk', 'running', 'run', 'approaching'),
    'rain': ('rain', 'raining', 'rainfall', 'downpour', 'drizzle', 'shower'),
    'thunder': ('thunder', 'storm', 'lightning', 'thunderstorm'),
    'wind': ('wind', 'windy', 'gust', 'gusts', 'blowing', 'breeze'),
    'water': ('water', 'river', 'stream', 'splash', 'splashes', 'flowing', 'waves', 'ocean'),
    'fire': ('fire', 'campfire', 'flame', 'flames', 'burning', 'burn', 'embers', 'sparks', 'crackle'),
    'insects': ('insect', 'insects', 'flies', 'mosquito', 'mosquitoes', 'buzzing', 'buzz', 'bee'),
    'birds': ('bird', 'birds', 'pigeon', 'pigeons', 'chirp', 'chirping', 'crow'),
    'wolves': ('wolf', 'wolves', 'howl', 'howling'),
    'animal': ('animal', 'animals', 'roar', 'growl', 'growling', 'predator', 'lion', 'bear'),
    'forest': ('forest', 'woods', 'jungle', 'trees', 'woodland'),
    'ice': ('ice', 'icy', 'freezing', 'frozen', 'snow', 'blizzard', 'winter'),
    'stone': ('stone', 'stones', 'rock', 'rocks', 'gravel', 'pebbles'),
    'bone': ('bone', 'bones', 'marrow', 'skeleton'),
    'chop': ('cut', 'cuts', 'cutting', 'chop', 'chopping', 'knife', 'slice', 'slicing'),
    'crack': ('crack', 'cracking', 'snap', 'snapping', 'breaking', 'breaks', 'broken', 'smash'),
    'impact': ('impact', 'hit', 'thud', 'slam', 'slam', 'crash', 'boom', 'bang', 'collision', 'thump'),
    'explosion': ('explosion', 'explode', 'exploding', 'blast', 'detonation'),
    'alert': ('alert', 'alarm', 'siren', 'warning', 'notification', 'emergency'),
    'phone': ('phone', 'mobile', 'telephone', 'ringtone', 'call', 'calling'),
    'radio': ('radio', 'static', 'walkie', 'speaker', 'broadcast'),
    'scream': ('scream', 'screaming', 'shout', 'shouting', 'yell', 'cry'),
    'zombie': ('zombie', 'zombies', 'undead', 'groan', 'groaning', 'moan'),
    'steps': ('stairs', 'staircase', 'climbing', 'climb'),
    'clock': ('clock', 'tick', 'ticking', 'watch', 'timer', 'countdown'),
    'electronic': ('computer', 'keyboard', 'button', 'click', 'clicking', 'beep', 'digital'),
    'camera': ('camera', 'shutter', 'photograph', 'photo', 'picture'),
    'transition': ('transition', 'whoosh', 'swoosh', 'swish', 'sweep'),
    'ding': ('ding', 'ping', 'chime', 'beep', 'notification'),
}

# Restrict abstract UI / movie hit effects to story beats that explicitly fit.
CATEGORIES_WITH_WEAK_CUES = {'transition', 'ding', 'electronic', 'impact'}


def words(value):
    return {w for w in re.findall(r'[a-z]+|\d+', value.lower()) if len(w) >= 3 and w not in SKIP_NAME}


def tags(text):
    tok = words(text)
    result = {category for category, aliases in EVENTS.items() if tok.intersection(aliases)}
    return tok, result


def list_audio(folder):
    if not folder.is_dir():
        return []
    return sorted((p for p in folder.rglob('*') if p.is_file() and p.suffix.lower() in FORMATS),
                  key=lambda p: str(p).lower())


def load_scenes(csv_path):
    if not csv_path.is_file():
        raise RuntimeError(f'SFX needs image timings: {csv_path}')
    output = []
    with csv_path.open('r', encoding='utf-8-sig', newline='') as f:
        for row in csv.DictReader(f):
            try:
                n = int(row['visual'])
                start, end = float(row['start']), float(row['end'])
            except (ValueError, KeyError, TypeError) as exc:
                raise RuntimeError(f'Invalid SFX timing row for VISUAL {row.get("visual")}: {exc}') from exc
            if not (0 <= start < end):
                raise RuntimeError(f'Invalid SFX interval for VISUAL {n}: {start} - {end}')
            output.append({'visual': n, 'start': start, 'end': end,
                           'sentence': row.get('sentence', '') or '',
                           'estimated': str(row.get('estimated', '')).lower() == 'true'})
    output.sort(key=lambda x: x['visual'])
    if not output or len({r['visual'] for r in output}) != len(output):
        raise RuntimeError('SFX image timings empty or contain duplicate VISUAL numbers.')
    if any(output[i]['visual'] != output[i-1]['visual'] + 1 for i in range(1, len(output))):
        raise RuntimeError('SFX timings have missing visual numbers; refusing uncertain grouping.')
    if any(output[i]['start'] < output[i-1]['end'] - 0.15 for i in range(1, len(output))):
        raise RuntimeError('SFX timings overlap unexpectedly.')
    return output


def catalogue(folder):
    sounds = []
    for p in list_audio(folder):
        # Folder name can describe otherwise generic downloaded SFX filenames.
        label = ' '.join([p.parent.name, p.stem]) if p.parent != folder else p.stem
        terms, classes = tags(label)
        sounds.append({'path': p, 'name': p.name, 'terms': terms, 'classes': classes})
    return sounds


def relevance(scene, sound):
    termset, scene_classes = tags(scene['sentence'])
    common = termset.intersection(sound['terms'])
    same_categories = scene_classes.intersection(sound['classes'])
    if not common and not same_categories:
        return 0
    # A filename's descriptive words are stronger evidence than generic sounds.
    score = len(common) * 6 + len(same_categories) * 4
    score += sum(3 for c in same_categories if c not in CATEGORIES_WITH_WEAK_CUES)
    if scene['estimated']:
        score -= 0.25
    return max(0, score)


def make_plan(timing_path, sound_folder):
    scenes = load_scenes(timing_path)
    sounds = catalogue(sound_folder)
    if not sounds:
        raise RuntimeError(f'No usable audio files (.mp3/.wav/.ogg/etc.) found in {sound_folder}')
    chosen = []
    previous_visual = -1000
    recently_used = Counter()
    first = scenes[0]['visual']
    for i in range(0, len(scenes), 3):
        chunk = scenes[i:i + 3]
        options = []
        for scene in chunk:
            if scene['visual'] - previous_visual < 3:  # real 3-image cooldown across group boundaries
                continue
            for sound in sounds:
                match = relevance(scene, sound)
                if match <= 0:
                    continue
                # Avoid repeating the same sound if another relevant one is available.
                penalty = min(12, recently_used[sound['path']] * 4)
                options.append((match - penalty, match, -scene['visual'], scene, sound))
        if not options:
            continue  # don't insert a random effect that does not fit narration
        options.sort(key=lambda x: (x[0], x[1], x[2], x[4]['name']), reverse=True)
        _, match, _, scene, sound = options[0]
        if match < 3:
            continue
        chosen.append({
            'visual': scene['visual'],
            'image_block': (scene['visual'] - first) // 3 + 1,
            'start': round(scene['start'], 3),
            'end': round(scene['end'], 3),
            'sentence': scene['sentence'],
            'effect_file': str(sound['path']),
            'effect_name': sound['name'],
            'match_score': round(match, 1),
        })
        previous_visual = scene['visual']
        recently_used[sound['path']] += 1
    # Independent assertion of BOTH constraints.
    groups = [r['image_block'] for r in chosen]
    if len(groups) != len(set(groups)) or any(b['visual'] - a['visual'] < 3 for a, b in zip(chosen, chosen[1:])):
        raise AssertionError('SFX one-per-three-images limit unexpectedly violated.')
    return scenes, sounds, chosen


def duration(path):
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                             '-of', 'default=noprint_wrappers=1:nokey=1', str(path)],
                            check=True, text=True, capture_output=True)
    return float(result.stdout.strip())


def prepare_events(plan, max_sfx_seconds=2.0):
    cache = {}
    events = []
    for item in plan:
        path = Path(item['effect_file'])
        try:
            if path not in cache:
                cache[path] = duration(path)
            natural = cache[path]
        except (ValueError, subprocess.CalledProcessError):
            print(f'SFX: unreadable audio skipped: {path.name}', flush=True)
            continue
        onset = item['start'] + min(0.20, (item['end'] - item['start']) * 0.12)
        usable = min(natural, max_sfx_seconds, item['end'] - onset - 0.05)
        if usable < 0.12:
            continue
        events.append({**item, 'play_at': round(onset, 3), 'play_seconds': round(usable, 3)})
    return events


def ffmpeg_mix(original, output, events, volume=0.065):
    if not 0.0 < volume <= 0.5:
        raise RuntimeError('SFX_VOLUME must be between 0 and 0.5')
    if not events:
        raise RuntimeError('SFX mix was requested without any playable effects.')
    with tempfile.TemporaryDirectory(prefix='youtube_sfx_') as tmp:
        graph_file = Path(tmp) / 'filters.txt'
        filters = ['[0:a:0]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[voice]']
        inputs = ['voice']
        command = ['ffmpeg', '-hide_banner', '-nostdin', '-y', '-loglevel', 'error',
                   '-threads', '2', '-i', str(original)]
        for index, event in enumerate(events, 1):
            command.extend(['-i', event['effect_file']])
            seconds = event['play_seconds']
            end_fade = min(0.10, seconds / 3)
            filters.append(
                f'[{index}:a:0]atrim=0:{seconds:.3f},asetpts=PTS-STARTPTS,'
                f'aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,'
                f'volume={volume:.4f},afade=t=out:st={max(0, seconds-end_fade):.3f}:d={end_fade:.3f},'
                f'adelay={int(round(event["play_at"] * 1000))}:all=1[s{index}]'
            )
            inputs.append(f's{index}')
        filters.append(''.join(f'[{name}]' for name in inputs)
                       + f'amix=inputs={len(inputs)}:duration=first:dropout_transition=0:normalize=0,'
                       + 'alimiter=limit=0.95[mixed]')
        graph_file.write_text(';\n'.join(filters), encoding='utf-8')
        command += ['-/filter_complex', str(graph_file), '-map', '0:v:0', '-map', '[mixed]',
                    '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', str(output)]
        subprocess.run(command, check=True)


def save_json(file, obj):
    file.parent.mkdir(parents=True, exist_ok=True)
    temporary = file.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, file)


def ensure_project_sfx(project):
    """Safe integration point immediately before thumbnail and YouTube publishing."""
    video = project.path('final_video.mp4')
    saved_original = project.path('final_video_without_sfx.mp4')
    timing = project.path('edit/timings_review.csv')
    plan_path = project.path('edit/sfx_plan.json')
    library = Path(os.getenv('YOUTUBE_SFX_DIR', r'C:\YouTubeAI\SFX'))

    if project.status('sfx') == 'done':
        if not video.is_file():
            raise RuntimeError('SFX previously completed but final_video.mp4 is now missing.')
        print('SFX: already added and verified; skipping (no doubled sound effects).', flush=True)
        return
    if not video.is_file() and not saved_original.is_file():
        raise RuntimeError(f'SFX final video not found: {video}')
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        raise RuntimeError('FFmpeg/FFprobe missing from PATH.')

    scenes, audio_files, matches = make_plan(timing, library)
    events = prepare_events(matches, max_sfx_seconds=float(os.getenv('SFX_MAX_SECONDS', '2.0')))
    if not events:
        # The user prefers relevant effects; never pad with arbitrary bells or UI sounds.
        save_json(plan_path, {'version': 1, 'status': 'no_relevant_sfx', 'visual_count': len(scenes),
                              'library_count': len(audio_files), 'events': []})
        print(f'SFX: {len(audio_files)} files scanned; no matching scene events. Original video unchanged.', flush=True)
        # Intentional: leave SFX unmarked so added library files are considered next run.
        return
    if not saved_original.is_file():
        try:
            os.link(video, saved_original)  # fast, byte-exact hardlink (on same NTFS volume)
        except OSError:
            shutil.copy2(video, saved_original)  # fallback for unsupported filesystems
    if not saved_original.is_file() or saved_original.stat().st_size < 10000:
        raise RuntimeError('Could not verify original video backup; no SFX applied.')

    save_json(plan_path, {'version': 1, 'status': 'planned', 'visual_count': len(scenes),
                          'library': str(library), 'library_count': len(audio_files),
                          'max_one_per_three_images': True, 'minimum_image_start_spacing': 3,
                          'volume': float(os.getenv('SFX_VOLUME', '0.065')),
                          'events': events})
    temporary = project.path('final_video_sfx_working.mp4')
    if temporary.exists():
        temporary.unlink()  # incomplete previous mix: original remains separately verified
    print(f'SFX: adding {len(events)} scene-matched effects across {len(scenes)} images '
          '(maximum 1 per 3 images).', flush=True)
    ffmpeg_mix(saved_original, temporary, events, volume=float(os.getenv('SFX_VOLUME', '0.065')))
    if not temporary.is_file() or temporary.stat().st_size < 10000:
        raise RuntimeError('SFX mix failed verification; original video backup was preserved.')
    mixed_duration = duration(temporary)
    orig_duration = duration(saved_original)
    if abs(mixed_duration - orig_duration) > 1.0:
        raise RuntimeError(f'SFX mix duration mismatch ({mixed_duration:.1f}s vs {orig_duration:.1f}s); original video preserved.')
    os.replace(temporary, video)
    project.set('sfx')
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    plan['status'] = 'done'
    save_json(plan_path, plan)
    print(f'SFX COMPLETE: {video}. Original narration video: {saved_original}.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Local video SFX pass; not a YouTube downloader.')
    parser.add_argument('--plan', type=Path, help='timings_review.csv for dry preview')
    parser.add_argument('--library', type=Path, default=Path(os.getenv('YOUTUBE_SFX_DIR', r'C:\YouTubeAI\SFX')))
    parser.add_argument('--out', type=Path, help='write proposed SFX JSON without touching video')
    options = parser.parse_args()
    if not options.plan:
        parser.error('Provide --plan path/to/timings_review.csv to preview a project plan.')
    scenes, sounds, matched = make_plan(options.plan, options.library)
    info = {'visuals': len(scenes), 'files': len(sounds), 'proposed_count': len(matched), 'effects': matched}
    if options.out:
        save_json(options.out, info)
    print(json.dumps(info, indent=2, ensure_ascii=False))
