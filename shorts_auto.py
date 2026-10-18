"""Automatic 60-second YouTube Shorts generation from final_video.mp4.

Creates portrait 1080x1920 shorts with:
- blurred background fill
- slightly zoomed main video in the center
- top label PART 1, PART 2, ...
- one-word captions with pop animation
- rounded black caption background behind each word
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
from pathlib import Path


def _run(cmd):
    subprocess.run(cmd, check=True)


def _ffprobe_duration(path: Path) -> float:
    cmd = [
        'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1', str(path)
    ]
    out = subprocess.check_output(cmd, text=True, errors='replace').strip()
    return float(out)


def _escape_sub_path(path: Path) -> str:
    text = str(path.resolve()).replace('\\', '/')
    text = text.replace(':', '\\:').replace("'", r"\'")
    return text


def _escape_drawtext(text: str) -> str:
    text = text.replace('\\', r'\\')
    text = text.replace(':', r'\:')
    text = text.replace("'", r"\'")
    text = text.replace('%', r'\%')
    return text


def _ass_time(seconds: float) -> str:
    centis = max(0, int(round(seconds * 100)))
    hours, rest = divmod(centis, 360000)
    minutes, rest = divmod(rest, 6000)
    secs, cs = divmod(rest, 100)
    return f'{hours}:{minutes:02d}:{secs:02d}.{cs:02d}'


def _ass_escape(word: str) -> str:
    return word.replace('\\', r'\\').replace('{', '').replace('}', '').replace('\\N', ' ')


def _load_words(transcript: Path):
    if not transcript.is_file():
        raise RuntimeError(f'Word-aligned transcript required for accurate Shorts captions: {transcript}')
    data = json.loads(transcript.read_text(encoding='utf-8-sig'))
    raw = data.get('words', [])
    words = []
    for entry in raw:
        try:
            a, b = float(entry['start']), float(entry['end'])
            w = str(entry.get('word', '')).strip()
            if b > a and w:
                words.append({'start': a, 'end': b, 'text': w})
        except (KeyError, ValueError, TypeError):
            continue
    words.sort(key=lambda x: x['start'])
    if not words:
        raise RuntimeError(f'No usable word-level timings in {transcript}; cannot sync captions accurately.')
    return words


def _approx_text_width(text: str, fontsize: int) -> int:
    # Heuristic width estimator for Impact-like uppercase text.
    narrow = set('I1JL!|')
    wide = set('MW@%QO')
    total = 0.0
    for ch in text.upper():
        if ch == ' ':
            total += 0.28
        elif ch in narrow:
            total += 0.33
        elif ch in wide:
            total += 0.71
        else:
            total += 0.49
    return max(90, int(round(total * fontsize)))


def _rounded_rect_path(width: int, height: int, radius: int) -> str:
    w = float(width)
    h = float(height)
    r = float(max(0, min(radius, width // 2, height // 2)))
    k = round(r * 0.55228475, 2)
    # ASS vector drawing with cubic Bezier rounded corners.
    return (
        f'm {r:.2f} 0 '
        f'l {w-r:.2f} 0 '
        f'b {w-r+k:.2f} 0 {w:.2f} {r-k:.2f} {w:.2f} {r:.2f} '
        f'l {w:.2f} {h-r:.2f} '
        f'b {w:.2f} {h-r+k:.2f} {w-r+k:.2f} {h:.2f} {w-r:.2f} {h:.2f} '
        f'l {r:.2f} {h:.2f} '
        f'b {r-k:.2f} {h:.2f} 0 {h-r+k:.2f} 0 {h-r:.2f} '
        f'l 0 {r:.2f} '
        f'b 0 {r-k:.2f} {r-k:.2f} 0 {r:.2f} 0'
    )


def _write_ass(words, part_start: float, part_end: float, path: Path):
    caption_y = 1200
    font_size = 140
    pad_x = 4
    pad_y = 4
    radius = 0

    ass = [
        '[Script Info]',
        'ScriptType: v4.00+',
        'PlayResX: 1080',
        'PlayResY: 1920',
        'ScaledBorderAndShadow: yes',
        'WrapStyle: 2',
        '',
        '[V4+ Styles]',
        'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding',
        'Style: Box,Arial,20,&H00000000,&H00000000,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1',
        'Style: Word,Bangers,140,&H0000F0FF,&H0000F0FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1',
        '',
        '[Events]',
        'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text',
    ]

    count = 0
    for item in words:
        a = max(item['start'], part_start)
        b = min(item['end'], part_end)
        if b - a < 0.045:
            continue

        text = _ass_escape(item['text'].upper())
        if not text:
            continue

        duration_ms = int((b - a) * 1000)
        snap = min(90, max(45, duration_ms // 3))
        settle = min(duration_ms, snap + 75)

        text_w = int(_approx_text_width(text, font_size) * 0.90)
        text_h = int(font_size * 0.90)
        box_w = min(975, text_w + pad_x * 2)
        box_h = text_h + pad_y * 2
        box_path = _rounded_rect_path(box_w, box_h, radius)

        pop = (
            r'\fscx78\fscy78'
            + rf'\t(0,{snap},\fscx117\fscy117)'
            + rf'\t({snap},{settle},\fscx100\fscy100)'
        )

        box_tags = (
            r'{\an5\pos(540,' + str(caption_y) + r')'
            + pop + r'\1c&H000000&\1a&H10&\bord0\shad0\p1}'
        )
        text_tags = (
            r'{\an5\pos(540,' + str(caption_y) + r')'
            + pop + r'\1c&H0000F0FF&\3c&H000000&\bord10\shad0}'
        )

        # ass.append(f'Dialogue: 0,{_ass_time(a-part_start)},{_ass_time(b-part_start)},Box,,0,0,0,,{box_tags}{box_path}{{\\p0}}')
        ass.append(f'Dialogue: 1,{_ass_time(a-part_start)},{_ass_time(b-part_start)},Word,,0,0,0,,{text_tags}{text}')
        count += 1

    if not count:
        raise RuntimeError(f'No word captions for short interval {part_start:.2f}-{part_end:.2f}.')
    path.write_text('\n'.join(ass) + '\n', encoding='utf-8-sig')
    return count


def _make_short(input_video: Path, output_video: Path, subtitle_file: Path, part_number: int, start_sec: float, end_sec: float):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        raise RuntimeError('FFmpeg/FFprobe missing from PATH.')

    font_path = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Fonts' / 'arialbd.ttf'
    font = _escape_sub_path(font_path)
    subs = _escape_sub_path(subtitle_file)
    part_text = _escape_drawtext(f'PART {part_number}')

    vf = (
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,boxblur=20:10,eq=brightness=-0.08:saturation=0.85[bg];"
        "[0:v]scale=1140:-2[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2[subbed];"
        f"[subbed]drawtext=fontfile='{font}':text='{part_text}':"
        "x=(w-text_w)/2:y=80:fontsize=72:fontcolor=white:"
        "box=1:boxcolor=black@0.65:boxborderw=18,"
        f"subtitles='{subs}'"
    )

    cmd = [
        'ffmpeg', '-hide_banner', '-nostdin', '-y',
        '-ss', f'{start_sec:.3f}', '-to', f'{end_sec:.3f}', '-i', str(input_video),
        '-filter_complex', vf,
        '-map', '0:a?',
        '-c:v', 'libx264', '-preset', 'medium', '-crf', '20',
        '-r', '30', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-b:a', '160k',
        '-movflags', '+faststart',
        str(output_video)
    ]
    _run(cmd)


def ensure_project_shorts(project):
    final_video = project.path('final_video.mp4')
    edit_dir = project.path('edit')
    timings = edit_dir / 'transcript.json'
    output_dir = project.path('shorts')
    manifest = output_dir / 'shorts_manifest.json'

    if not final_video.is_file():
        raise RuntimeError(f'Long video missing: {final_video}')
    if not timings.is_file():
        raise RuntimeError(f'transcript.json missing: {timings}')

    segment_seconds = max(15, int(float(os.getenv('SHORTS_SEGMENT_SECONDS', '60'))))
    enabled = os.getenv('YOUTUBE_CREATE_SHORTS', '1').strip().lower()
    if enabled in ('0', 'false', 'no', 'off'):
        print('SHORTS: disabled by YOUTUBE_CREATE_SHORTS=0; skipping.', flush=True)
        return

    total_duration = _ffprobe_duration(final_video)
    total_parts = int(math.ceil(total_duration / float(segment_seconds)))
    output_dir.mkdir(parents=True, exist_ok=True)

    if os.getenv('SHORTS_TEST_PART', '0') == '0' and project.status('shorts') == 'done' and manifest.is_file():
        try:
            data = json.loads(manifest.read_text(encoding='utf-8'))
            files = [output_dir / item['file'] for item in data.get('parts', [])]
            if data.get('version') == 3 and len(files) == total_parts and all(p.is_file() and p.stat().st_size > 10000 for p in files):
                print(f'SHORTS: already generated and verified ({len(files)} parts); skipping.', flush=True)
                return
        except Exception:
            pass

    items = _load_words(timings)
    manifest_data = {
        'version': 3,
        'segment_seconds': segment_seconds,
        'source_video': str(final_video),
        'total_duration': total_duration,
        'total_parts': total_parts,
        'style': 'word_pop_rounded_box',
        'parts': []
    }

    test_part = int(os.getenv('SHORTS_TEST_PART', '0'))
    if test_part and not (1 <= test_part <= total_parts):
        raise RuntimeError(f'SHORTS_TEST_PART must be from 1 to {total_parts}.')
    print(f'SHORTS: creating {total_parts} short(s) from {total_duration:.1f}s long video.', flush=True)

    for index in range(total_parts):
        part_num = index + 1
        if test_part and part_num != test_part:
            continue
        part_start = index * segment_seconds
        part_end = min(total_duration, (index + 1) * segment_seconds)
        out_name = f'short_part_{part_num:02d}.mp4'
        ass_name = f'short_part_{part_num:02d}.ass'
        out_path = output_dir / out_name
        ass_path = output_dir / ass_name

        _write_ass(items, part_start, part_end, ass_path)
        if out_path.exists():
            out_path.unlink()
        print(f'SHORTS: rendering PART {part_num}/{total_parts} ({part_start:.1f}s - {part_end:.1f}s).', flush=True)
        _make_short(final_video, out_path, ass_path, part_num, part_start, part_end)
        if not out_path.is_file() or out_path.stat().st_size < 10000:
            raise RuntimeError(f'SHORTS output failed verification: {out_path}')

        manifest_data['parts'].append({
            'part': part_num,
            'start': round(part_start, 3),
            'end': round(part_end, 3),
            'file': out_name,
            'subtitle_file': ass_name,
        })

    if not test_part:
        manifest.write_text(json.dumps(manifest_data, ensure_ascii=False, indent=2), encoding='utf-8')
        project.set('shorts')
    print(f'SHORTS: rendered {len(manifest_data["parts"])} part(s) in {output_dir}', flush=True)








