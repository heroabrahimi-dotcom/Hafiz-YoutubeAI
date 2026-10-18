import os
"""Official YouTube Data API v3 publishing for AncientYouTubeAuto.
One-time Desktop OAuth, resumable media transfer, private-first publishing.
"""
import json, os, re, time, random
from pathlib import Path

SCOPES=['https://www.googleapis.com/auth/youtube.upload',
        'https://www.googleapis.com/auth/youtube.force-ssl']

def _save_json(path, obj):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(obj,indent=2,ensure_ascii=False),encoding='utf-8')
    os.replace(tmp,path)

def _youtube(base):
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError('YouTube packages missing. Run YouTubeUploadIntegration\\Install.ps1 first.') from exc
    private=Path(base)/'youtube_private'
    credentials_file=private/'client_secret.json'
    mode=os.environ.get('YOUTUBE_MODE','ancient').lower()
    token_file=private/('token_survival.json' if mode=='survival' else 'token.json')
    if not credentials_file.is_file():
        raise RuntimeError(f'YouTube OAuth not configured. Put your Desktop OAuth JSON here: {credentials_file}')
    data=json.loads(credentials_file.read_text(encoding='utf-8-sig'))
    if 'installed' not in data:
        raise RuntimeError('OAuth JSON must be a Desktop app (installed) client, not a service account or API key.')
    creds=None
    if token_file.exists():
        creds=Credentials.from_authorized_user_file(str(token_file),SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow=InstalledAppFlow.from_client_secrets_file(str(credentials_file),SCOPES)
            creds=flow.run_local_server(host='localhost',port=0,open_browser=True,
                                        authorization_prompt_message='Open this URL to authorize YouTube uploading: {url}')
        _save_json(token_file,json.loads(creds.to_json()))
    return build('youtube','v3',credentials=creds,cache_discovery=False)

def _metadata(project):
    title=re.sub(r'\s+',' ',str(project.data['title'])).strip()[:100]
    content=' '.join((project.path(f'script/part_{i}.txt').read_text(encoding='utf-8')
                     for i in (1,2) if project.path(f'script/part_{i}.txt').exists()))
    if project.data.get('mode','ancient')=='survival':
        terms=['survival story','animated story','survival animation','disaster survival',
               'fictional survival','2D animation','survival challenge']
        for word in ['zombie','outbreak','water','fire','roof','storm','apartment','city','escape','food']:
            if re.search(r'\b'+re.escape(word)+r'\b',(title+' '+content).lower()):terms.append(word)
        desc=(f'{title}\n\nAn original fictional animated survival story exploring tough choices, '
              'resource shortages, danger and the will to survive.\n\n'
              'This story is fictional, presented as entertainment.\n\n'
              '#SurvivalStory #Animation #AnimatedStories')
        result={'title':title,'description':desc[:5000],'tags':terms,'categoryId':'24'}
        _save_json(project.path('youtube_metadata.json'),result)
        return result
    # Content-derived keywords rather than invented claims/hashtags.
    terms=['ancient humans','prehistoric survival','human evolution','archaeology',
           'anthropology','early humans','prehistoric history','hunter gatherers']
    for word in ['fire','teeth','water','ice age','tools','food','shelter','language','hunting','medicine']:
        if re.search(r'\b'+re.escape(word)+r'\b',(title+' '+content).lower()):terms.append(word)
    desc=(f'{title}\n\nExplore how prehistoric humans survived, adapted, and developed '
          'the skills that shaped our history. This educational documentary examines '
          'evidence and explanations from archaeology and human evolution.\n\n'
          'Topics: ancient survival, anthropology, early humans, and prehistoric life.\n\n'
          '#AncientHumans #Prehistory #HumanEvolution')
    result={'title':title,'description':desc[:5000],'tags':terms,'categoryId':'27'}
    _save_json(project.path('youtube_metadata.json'),result)
    return result

def _thumbnail(project):
    """Return the independently generated and verified thumbnail, never a placeholder frame."""
    dest=project.path('thumbnail/thumbnail.jpg')
    if not dest.is_file() or dest.stat().st_size<15_000:
        raise RuntimeError(f'AI thumbnail is missing: {dest}. The pipeline must generate it before YouTube upload.')
    return dest


def _upload_video(project):
    """Fail closed rather than silently select an SFX mix for Survival."""
    requested=os.environ.get('YOUTUBE_UPLOAD_NO_SFX_FILE','').strip().strip('"')
    if requested:
        candidate=Path(requested)
        if not candidate.is_absolute():
            candidate=project.path(requested)
    elif os.environ.get('YOUTUBE_MODE','ancient').lower()=='survival':
        names=('final_video_no_sfx.mp4','final_video_nosfx.mp4',
               'final_video_without_sfx.mp4','video_no_sfx.mp4',
               'final_video_clean.mp4')
        candidates=[project.path(name) for name in names if project.path(name).is_file()]
        if not candidates:
            raise RuntimeError(
                'No explicitly identified no-SFX MP4. Set YOUTUBE_UPLOAD_NO_SFX_FILE '
                'to the exact path of your completed narration-only video. '
                'Upload stopped before any API attempt; final_video.mp4 may contain SFX.')
        candidate=candidates[0]
    else:
        candidate=project.path('final_video_without_sfx')
    if not candidate.is_file() or candidate.stat().st_size<100_000:
        raise RuntimeError(f'No complete upload MP4 at {candidate}. Upload not attempted.')
    print('UPLOAD VIDEO SELECTED: '+str(candidate),flush=True)
    return candidate


def _is_upload_limit_exceeded(exc):
    if getattr(getattr(exc,'resp',None),'status',None)!=400:
        return False
    content=getattr(exc,'content',b'')
    if isinstance(content,bytes):
        content=content.decode('utf-8',errors='replace')
    try:
        payload=json.loads(content)
        reasons=[str(e.get('reason','')) for e in payload.get('error',{}).get('errors',[])]
        if 'uploadLimitExceeded' in reasons:
            return True
    except (TypeError,ValueError,AttributeError):
        pass
    return 'uploadLimitExceeded' in str(content)


def publish(project,base,visibility='private'):
    """Never retry an uncertain videos.insert, to avoid duplicate published videos."""
    if visibility not in ('private','unlisted','public'):raise ValueError('Invalid YouTube visibility')
    if project.status('youtube')=='done':return
    video=_upload_video(project)
    meta_file=project.path('youtube_metadata.json')
    metadata=json.loads(meta_file.read_text('utf-8')) if meta_file.exists() else _metadata(project)
    thumb=_thumbnail(project)
    youtube=_youtube(base)
    # Each mode has a separate OAuth authorization; verify target channel before any upload.
    selected=os.environ.get('YOUTUBE_MODE','ancient').lower()
    actual=youtube.channels().list(part='snippet',mine=True).execute().get('items',[])
    if len(actual)!=1:raise RuntimeError('Could not verify exactly one authorized YouTube channel; upload stopped.')
    channel=actual[0]
    print('UPLOAD TARGET ['+selected+']: '+channel['snippet']['title']+' ('+channel['id']+')',flush=True)
    expected=os.environ.get('YOUTUBE_'+selected.upper()+'_CHANNEL_ID','').strip()
    if not expected:raise RuntimeError('Channel ID not configured for '+selected+'. Set YOUTUBE_'+selected.upper()+'_CHANNEL_ID to '+channel['id']+' after verifying in YouTube Studio; upload stopped safely.')
    if channel['id']!=expected:raise RuntimeError('Wrong YouTube channel authorized: '+channel['id']+'; expected '+expected+'. Upload stopped.')
    from googleapiclient.http import MediaFileUpload
    from googleapiclient.errors import HttpError
    state=project.data.setdefault('youtube_upload',{})
    video_id=state.get('video_id')
    if not video_id:
        if state.get('insert_attempted'):
            raise RuntimeError('Earlier upload may have succeeded but its ID was not saved. Check YouTube Studio before retrying to prevent a duplicate. No regeneration.')
        state['insert_attempted']=True
        project.save()
        body={'snippet':metadata,'status':{'privacyStatus':visibility,'selfDeclaredMadeForKids':False}}
        request=youtube.videos().insert(part='snippet,status',body=body,
              media_body=MediaFileUpload(str(video),mimetype='video/mp4',chunksize=8*1024*1024,resumable=True))
        response=None;retries=0
        while response is None:
            try:
                progress,response=request.next_chunk()
                retries=0
                if progress:print('YouTube upload: {:.0%}'.format(progress.progress()),flush=True)
            except (OSError,HttpError) as exc:
                status=getattr(getattr(exc,'resp',None),'status',None)
                if isinstance(exc,HttpError) and _is_upload_limit_exceeded(exc):
                    # YouTube explicitly rejected this insert, so no video ID was issued.
                    # Record rejection and permit a later user-triggered retry; do not
                    # automatically retry a daily account upload cap.
                    state['insert_attempted']=False
                    state['upload_limit_blocked_at']=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
                    state['upload_limit_video']=str(video)
                    project.save()
                    raise RuntimeError(
                        'YouTube upload limit reached. No video was accepted. '
                        'Local video and thumbnail remain unchanged. Retry after the '
                        'account upload limit resets; no automatic retry was made.') from exc
                if isinstance(exc,HttpError) and status not in (429,500,502,503,504):raise
                retries+=1
                if retries>6:raise
                time.sleep(min(2**retries+random.random(),60))
        video_id=response.get('id')
        if not video_id:raise RuntimeError('YouTube did not return a video ID; inspect Studio before retrying.')
        state['video_id']=video_id
        project.save()
    if not state.get('thumbnail_done'):
        youtube.thumbnails().set(videoId=video_id,media_body=MediaFileUpload(str(thumb),mimetype='image/jpeg')).execute()
        state['thumbnail_done']=True
        project.save()
    link=f'https://www.youtube.com/watch?v={video_id}'
    project.path('youtube_url.txt').write_text(link+'\n',encoding='utf-8')
    project.set('youtube')
    print('YOUTUBE UPLOADED: '+link,flush=True)
