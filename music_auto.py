"""Optional survival-only music pass. Never re-mix on project resume."""
from pathlib import Path
import os, subprocess, shutil, json

def _duration(path):
    return float(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1',str(path)],text=True).strip())

def ensure_survival_music(project):
    if os.getenv('YOUTUBE_MODE','ancient').lower()!='survival':
        return
    video=project.path('final_video.mp4')
    backup=project.path('final_video_without_music.mp4')
    working=project.path('final_video_music_working.mp4')
    if project.status('music')=='done':
        if not video.is_file():raise RuntimeError('Music marked complete, but final video is missing.')
        print('MUSIC: already completed; skipping double mix.',flush=True)
        return
    library=Path(os.getenv('YOUTUBE_MUSIC_DIR',r'C:\YouTubeAI\Music\Survival'))
    tracks=sorted([f for f in library.glob('*') if f.is_file() and f.suffix.lower() in ('.mp3','.wav','.m4a','.ogg','.flac')]) if library.is_dir() else []
    if not tracks:
        print(f'MUSIC: no licensed track in {library}; leaving video unchanged. Add a track to enable suspense ambience.',flush=True)
        return
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        raise RuntimeError('FFmpeg/FFprobe missing; music not applied.')
    # Deterministic source selection, no unpredictable changes between resumes.
    track=tracks[0]
    if not backup.is_file():
        if not video.is_file():raise RuntimeError('Music: input video missing')
        try:os.link(video,backup)
        except OSError:shutil.copy2(video,backup)
    if backup.stat().st_size<10000:raise RuntimeError('Music: original-backup verification failed')
    # If previous run died mid-mix, backup is the sole authoritative source.
    if working.exists():working.unlink()
    print(f'MUSIC: adding subtle suspense bed from {track.name} (Survival only).',flush=True)
    # Background low gain; 1.5s fades, audio limiter; video stream unchanged.
    length=_duration(backup)
    fade_start=max(0.0,length-1.5)
    fc=('[0:a:0]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[voice];'
        '[1:a:0]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,'
        f'volume=0.07,atrim=0:{length:.3f},afade=t=in:st=0:d=1.5,afade=t=out:st={fade_start:.3f}:d=1.5[bed];'
        '[voice][bed]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,'
        'alimiter=limit=0.95[mixed]')
    command=['ffmpeg','-hide_banner','-nostdin','-y','-loglevel','error','-threads','2',
             '-i',str(backup),'-stream_loop','-1','-i',str(track),'-filter_complex',fc,
             '-map','0:v:0','-map','[mixed]','-c:v','copy','-c:a','aac','-b:a','192k',
             '-movflags','+faststart',str(working)]
    subprocess.run(command,check=True)
    if not working.is_file() or working.stat().st_size<10000 or abs(_duration(working)-length)>1.0:
        raise RuntimeError('Music output verification failed; unmodified source backed up.')
    os.replace(working,video)
    project.set('music')
    plan=project.path('edit/music_plan.json')
    plan.parent.mkdir(parents=True,exist_ok=True)
    plan.write_text(json.dumps({'track':str(track),'gain':0.07,'status':'done','backup':str(backup)},indent=2),encoding='utf-8')
    print('MUSIC: complete. Original pre-music video backed up.',flush=True)
