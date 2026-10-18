"""Complete missing Shorts and restore a valid manifest without regenerating finished parts.

Uses the CURRENT installed shorts_auto.py, retaining the user's edited caption styling.
"""
from __future__ import annotations
import argparse
import json
import math
import os
import sys
from pathlib import Path


def atomic_json(path, value):
    t=path.with_suffix(path.suffix+'.working')
    t.write_text(json.dumps(value,indent=2,ensure_ascii=False),encoding='utf-8')
    t.replace(path)


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--root',required=True)
    ap.add_argument('--project',required=True)
    args=ap.parse_args()
    root=Path(args.root).resolve()
    project=Path(args.project).resolve()
    sys.path.insert(0,str(root))
    from shorts_auto import _ffprobe_duration, _load_words, _write_ass, _make_short

    source=project/'final_video.mp4'
    transcript=project/'edit'/'transcript.json'
    out=project/'shorts'
    meta=project/'project.json'
    if not source.is_file(): raise RuntimeError(f'Source video missing: {source}')
    if not transcript.is_file(): raise RuntimeError(f'Word timings missing: {transcript}')
    if not meta.is_file(): raise RuntimeError(f'Project metadata missing: {meta}')
    out.mkdir(exist_ok=True)
    segment=60
    length=_ffprobe_duration(source)
    count=math.ceil(length/segment)
    manifest_file=out/'shorts_manifest.json'
    previous=json.loads(manifest_file.read_text(encoding='utf-8-sig')) if manifest_file.exists() else {}
    if previous and previous.get('source_video') and Path(previous['source_video']).resolve()!=source:
        raise RuntimeError('Manifest source video differs; stopping safely.')
    expected=[]
    for index in range(count):
        part=index+1
        a=float(index*segment)
        b=float(min(length,(index+1)*segment))
        expected.append({'part':part,'start':round(a,3),'end':round(b,3),'file':f'short_part_{part:02d}.mp4','subtitle_file':f'short_part_{part:02d}.ass'})
    if previous and previous.get('total_parts') not in (None,count):
        raise RuntimeError('Manifest part count differs from current source; stopping safely.')
    words=None
    for item in expected:
        part=item['part']
        output=out/item['file']
        if output.is_file() and output.stat().st_size>50000:
            actual=_ffprobe_duration(output)
            wanted=item['end']-item['start']
            if abs(actual-wanted)<1.5:
                print(f'PART {part}/{count}: completed file verified; keeping it.',flush=True)
                continue
            raise RuntimeError(f'Existing PART {part} has unexpected duration {actual:.2f}s vs {wanted:.2f}s. Review manually; not overwriting.')
        if output.exists():
            raise RuntimeError(f'PART {part}: incomplete or empty output exists: {output}. Check it before deleting; not overwriting.')
        if words is None: words=_load_words(transcript)
        ass_path=out/item['subtitle_file']
        print(f'PART {part}/{count}: creating missing clip with your current captions...',flush=True)
        _write_ass(words,item['start'],item['end'],ass_path)
        temporary=out/f'_part_{part:02d}_working.mp4'
        if temporary.exists():
            raise RuntimeError(f'Unfinished render temp exists: {temporary}. Review and remove before retrying.')
        _make_short(source,temporary,ass_path,part,item['start'],item['end'])
        if not temporary.is_file() or temporary.stat().st_size<50000:
            raise RuntimeError(f'Clip render failed: {temporary}')
        actual=_ffprobe_duration(temporary)
        if abs(actual-(item['end']-item['start']))>1.5:
            raise RuntimeError(f'Render duration mismatch for PART {part}: {actual:.2f}s')
        temporary.replace(output)
        print(f'PART {part}/{count}: finished.',flush=True)
    manifest={'version':3,'segment_seconds':segment,'source_video':str(source),
              'total_duration':length,'total_parts':count,'parts':expected}
    atomic_json(manifest_file,manifest)
    metadata=json.loads(meta.read_text(encoding='utf-8-sig'))
    metadata.setdefault('status',{})['shorts']='done'
    atomic_json(meta,metadata)
    print(f'ALL {count} SHORTS COMPLETED; manifest saved at {manifest_file}',flush=True)

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print('STOPPED: '+str(exc),file=sys.stderr,flush=True)
        sys.exit(1)
