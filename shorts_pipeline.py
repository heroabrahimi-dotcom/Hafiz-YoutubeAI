"""Post-upload Shorts stage: exactly 2 x 50-second vertical clips, resumable uploads.
Integrates with AncientYouTubeAuto; no main-video regeneration or changes to original files.
"""
import argparse
import json
import math
import os
import subprocess
from pathlib import Path

TARGET_SURVIVAL_CHANNEL = 'UCDTlS5a9wh2pyU0yd9q19iQ'

def _duration(file):
    s=subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration','-of','default=nokey=1:noprint_wrappers=1',str(file)],text=True)
    return float(s.strip())

def _atomic_json(path, state):
    tmp=path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(state,indent=2,ensure_ascii=False),encoding='utf-8')
    os.replace(tmp,path)

def _render(source, target, start, duration, number):
    # Keep the full 16:9 storytelling frame visible, with a blurred backdrop.
    vf=('split=2[bg][fg];'
        '[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=24:2[back];'
        '[fg]scale=1080:1920:force_original_aspect_ratio=decrease[front];'
        '[back][front]overlay=(W-w)/2:(H-h)/2,format=yuv420p')
    cmd=['ffmpeg','-hide_banner','-loglevel','error','-nostdin','-y','-ss',f'{start:.3f}',
         '-i',str(source),'-t',f'{duration:.3f}','-filter_complex',vf,
         '-map','[v]','-map','0:a:0','-c:v','libx264','-preset','veryfast','-crf','20',
         '-r','30','-pix_fmt','yuv420p','-c:a','aac','-b:a','160k',
         '-movflags','+faststart',str(target)]
    cmd[cmd.index('-map')+1]='[v]' # explicit output from filter below
    cmd[cmd.index('-filter_complex')+1]=vf+'[v]'
    subprocess.run(cmd,check=True)
    if target.stat().st_size < 100000:
        raise RuntimeError(f'Short {number} too small; not accepting output')
    if abs(_duration(target)-duration)>2:
        raise RuntimeError(f'Short {number} duration verification failed')

def _youtube_authenticated(base):
    from youtube_publish import _youtube
    return _youtube(base)

def ensure_shorts(project, base, visibility='private', upload=True):
    """Produce 2 Shorts and publish to authenticated HeroApx only; journal every upload intent."""
    root=Path(project.path('final_video.mp4')).parent
    original=root/'final_video.mp4'
    if not original.is_file() or original.stat().st_size<100000:
        raise RuntimeError('SHORTS: existing main video not found; no new generation attempted')
    if visibility not in ('private','unlisted','public'):
        raise ValueError('Invalid visibility')
    video_seconds=_duration(original)
    if video_seconds<110:
        raise RuntimeError('SHORTS: main video too short to make two distinct clips')
    outdir=root/'shorts'
    outdir.mkdir(exist_ok=True)
    journal=outdir/'shorts_upload_state.json'
    state=json.loads(journal.read_text(encoding='utf-8-sig')) if journal.is_file() else {}
    if not isinstance(state,dict):raise RuntimeError('SHORTS: invalid journal')
    main_url=root/'youtube_url.txt'
    main_link=main_url.read_text(encoding='utf-8').strip() if main_url.is_file() else ''
    # Favor two separated passages; position is deterministic across resume runs.
    duration=50.0
    starts=[max(0.0,min(45.0,video_seconds-duration)), max(0.0,min(video_seconds*0.55,video_seconds-duration))]
    title=str(project.data.get('title','Survival Story')).strip()
    youtube=None
    for idx,start in enumerate(starts,1):
        key=str(idx)
        entry=state.setdefault(key,{})
        dest=outdir/f'short_{idx:02d}.mp4'
        if not dest.is_file() or dest.stat().st_size<100000:
            print(f'SHORTS: making vertical Part {idx} from {start:.1f}s...',flush=True)
            _render(original,dest,start,duration,idx)
        else:
            print(f'SHORTS: Short {idx} already exists; preserving.',flush=True)
        if not upload:
            continue
        if entry.get('video_id'):
            print('SHORTS: Part {} already uploaded: https://www.youtube.com/watch?v={}'.format(idx,entry['video_id']),flush=True)
            continue
        if entry.get('insert_attempted'):
            raise RuntimeError(f'SHORTS: upload Part {idx} uncertain. Check YouTube Studio before retrying; no duplicate upload.')
        if youtube is None:
            youtube=_youtube_authenticated(base)
            channels=youtube.channels().list(part='id,snippet',mine=True).execute().get('items',[])
            actual={c.get('id') for c in channels}
            if TARGET_SURVIVAL_CHANNEL not in actual:
                raise RuntimeError('SHORTS: OAuth channel not HeroApx. Refusing upload to wrong channel. Files are rendered locally.')
        from googleapiclient.http import MediaFileUpload
        snippet={'title':(f'{title} | Part {idx} #Shorts')[:100],
                 'description':f'From the animated survival story: {title}\n\nFull story: {main_link}\n\n#Shorts #SurvivalStory',
                 'categoryId':'24'}
        entry['insert_attempted']=True
        _atomic_json(journal,state)
        print(f'SHORTS: uploading Part {idx} to HeroApx...',flush=True)
        request=youtube.videos().insert(part='snippet,status',body={'snippet':snippet,'status':{'privacyStatus':visibility,'selfDeclaredMadeForKids':False}},media_body=MediaFileUpload(str(dest),mimetype='video/mp4',chunksize=8*1024*1024,resumable=True))
        response=None
        while response is None:
            progress,response=request.next_chunk()
            if progress: print(f'SHORTS: Part {idx} upload {progress.progress():.0%}',flush=True)
        video_id=(response or {}).get('id')
        if not video_id:
            raise RuntimeError('SHORTS: video ID not returned. Inspect YouTube Studio; no automatic retry.')
        entry['video_id']=video_id
        _atomic_json(journal,state)
        print(f'SHORTS UPLOADED: https://www.youtube.com/watch?v={video_id}',flush=True)
    if all(state.get(str(i),{}).get('video_id') for i in (1,2)) if upload else all((outdir/f'short_{i:02d}.mp4').exists() for i in (1,2)):
        project.set('shorts')
    print('SHORTS: finished post-upload stage.',flush=True)

def backfill():
    ap=argparse.ArgumentParser()
    ap.add_argument('--project',required=True)
    ap.add_argument('--base',required=True)
    ap.add_argument('--visibility',choices=['private','unlisted','public'],default='private')
    ap.add_argument('--create-only',action='store_true')
    a=ap.parse_args()
    root=Path(a.project)
    state_file=root/'state.json'
    if not state_file.exists():
        # Try conventional state names when copying a project only.
        for name in ('project.json','progress.json'):
            if (root/name).is_file(): state_file=root/name;break
    class Project:
        def __init__(self):
            self.data={}
            if state_file.exists():
                data=json.loads(state_file.read_text(encoding='utf-8-sig'))
                if isinstance(data,dict):self.data=data
            self.data.setdefault('title',root.name.replace('-',' ').title())
        def path(self,name):return root/name
        def set(self,key):pass # Already-uploaded project; shorts journal is authoritative.
    ensure_shorts(Project(),Path(a.base),a.visibility,upload=not a.create_only)

if __name__=='__main__': backfill()
