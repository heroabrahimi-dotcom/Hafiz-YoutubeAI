"""Upload all Shorts private now, then publish the first 2 and set YouTube publishAt for the rest.

Uses existing Survival OAuth and private_upload_state.json. Never automatically retries
uncertain video inserts. Stores a fixed schedule anchored to the first run.
"""
from __future__ import annotations
import argparse, datetime as dt, json, os, sys
from pathlib import Path

UTC = dt.timezone.utc

def save(path, data):
    t=path.with_suffix('.json.tmp');t.write_text(json.dumps(data,indent=2,ensure_ascii=False),encoding='utf-8');os.replace(t,path)

def iso(t):
    return t.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')

def parse(t):
    return dt.datetime.fromisoformat(t.replace('Z','+00:00')).astimezone(UTC)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--project',required=True)
    ap.add_argument('--root',required=True)
    args=ap.parse_args()
    project=Path(args.project);root=Path(args.root);shorts=project/'shorts'
    manifest=shorts/'shorts_manifest.json'
    if not manifest.is_file():raise RuntimeError('All Shorts must be rendered first: missing shorts_manifest.json.')
    m=json.loads(manifest.read_text(encoding='utf-8-sig'))
    parts=m.get('parts',[]);total=int(m.get('total_parts',0))
    if total!=15 or len(parts)!=15 or [p.get('part') for p in parts]!=list(range(1,16)):
        raise RuntimeError(f'Expected all 15 consecutive Shorts. Manifest shows {len(parts)}/{total}.')
    for p in parts:
        f=shorts/p['file']
        if not f.is_file() or f.stat().st_size<50000:raise RuntimeError(f'Missing/invalid Short: {f}')
    expected=os.environ.get('YOUTUBE_SURVIVAL_CHANNEL_ID','').strip()
    if not expected:raise RuntimeError('YOUTUBE_SURVIVAL_CHANNEL_ID missing. Set it after verifying in YouTube Studio.')
    os.environ['YOUTUBE_MODE']='survival'
    sys.path.insert(0,str(root))
    from youtube_publish import _youtube
    from googleapiclient.http import MediaFileUpload
    yt=_youtube(root)
    ch=yt.channels().list(part='snippet',mine=True).execute().get('items',[])
    if len(ch)!=1 or ch[0]['id']!=expected:raise RuntimeError(f'Channel mismatch: expected {expected}, found {[(x.get("id"),x.get("snippet",{}).get("title")) for x in ch]}. STOPPED.')
    print(f'CONFIRMED Survival channel: {ch[0]["snippet"]["title"]} ({expected})',flush=True)
    title=' '.join(json.loads((project/'project.json').read_text(encoding='utf-8-sig')).get('title','Survival Story').split())
    state_file=shorts/'private_upload_state.json'
    state=json.loads(state_file.read_text(encoding='utf-8-sig')) if state_file.exists() else {'version':1,'channel_id':expected,'parts':{}}
    if state.get('channel_id')!=expected:raise RuntimeError('Existing state references a different channel.')
    state.setdefault('parts',{})
    # Preserve existing anchor when present, but create it only after ALL uploads.
    # This avoids scheduling deadlines expiring during lengthy uploads.
    for p in parts:
        i=p['part'];key=str(i);record=state['parts'].get(key,{})
        if record.get('video_id'):
            print(f'UPLOAD {i}/15: existing {record["video_id"]} (will verify before publishing)',flush=True)
            continue
        if record.get('insert_attempted'):
            raise RuntimeError(f'Part {i} has uncertain insert. Check YouTube Studio, then reconcile the ID in {state_file}; no duplicate upload attempted.')
        tail=f' | Part {i}/15 #Shorts'
        name=title[:100-len(tail)].rstrip()+tail
        body={'snippet':{'title':name,'description':f'{title}\nPart {i} of 15.\nFictional animated survival story.\n#Shorts #SurvivalStory #Animation','tags':['Shorts','SurvivalStory','Animation'],'categoryId':'24'},'status':{'privacyStatus':'private','selfDeclaredMadeForKids':False}}
        state['parts'][key]={'insert_attempted':True,'file':p['file'],'title':name}
        save(state_file,state)
        print(f'UPLOAD {i}/15: uploading PRIVATE',flush=True)
        req=yt.videos().insert(part='snippet,status',body=body,media_body=MediaFileUpload(str(shorts/p['file']),mimetype='video/mp4',resumable=True,chunksize=8*1024*1024))
        response=None
        while response is None:
            progress,response=req.next_chunk()
            if progress:print(f'  {progress.progress():.0%}',flush=True)
        vid=(response or {}).get('id')
        if not vid:raise RuntimeError(f'Part {i} upload response has no ID; check Studio before resuming')
        state['parts'][key].update(video_id=vid,privacy_status='private')
        save(state_file,state)
        print(f'  Uploaded: https://www.youtube.com/watch?v={vid}',flush=True)
    print('All 15 uploaded or previously recorded. Applying PUBLIC/SCHEDULED statuses...',flush=True)
    # First successful full upload batch becomes schedule anchor.
    if 'schedule_anchor_utc' not in state:
        state['schedule_anchor_utc']=iso(dt.datetime.now(UTC))
        save(state_file,state)
    anchor=parse(state['schedule_anchor_utc'])
    now=dt.datetime.now(UTC)
    # YouTube rejects publishAt values in the past. STOP rather than silently publishing early.
    for i in range(3,16):
        publish_at=anchor+dt.timedelta(hours=(i-2))
        if publish_at<=now+dt.timedelta(minutes=2):
            raise RuntimeError(f'Schedule time for Part {i} ({iso(publish_at)}) is already too close/past. Uploads remain private; adjust schedule_anchor_utc in state after reviewing, then rerun.')
    for i in range(1,16):
        record=state['parts'][str(i)];vid=record['video_id']
        result=yt.videos().list(part='status,snippet',id=vid).execute().get('items',[])
        if len(result)!=1:raise RuntimeError(f'Part {i}: uploaded video ID {vid} not readable; stopped.')
        v=result[0]
        actual_channel=v.get('snippet',{}).get('channelId')
        if actual_channel!=expected:raise RuntimeError(f'Part {i}: video belongs to {actual_channel}, not {expected}; stopped.')
        current=v.get('status',{})
        if current.get('privacyStatus')=='public' and i>2:
            raise RuntimeError(f'Part {i} is already public; cannot undo without explicit approval.')
        scheduled_time=None if i<=2 else iso(anchor+dt.timedelta(hours=(i-2)))
        if i<=2 and current.get('privacyStatus')=='public':
            record.update(publishing='public',publish_attempted=False);save(state_file,state)
            print(f'PART {i}: already PUBLIC',flush=True);continue
        if scheduled_time and current.get('privacyStatus')=='private' and current.get('publishAt') and abs((parse(current['publishAt'])-parse(scheduled_time)).total_seconds())<2:
            record.update(publishing='scheduled',publish_at=scheduled_time,publish_attempted=False)
            save(state_file,state);print(f'PART {i}: already scheduled {scheduled_time}',flush=True);continue
        # Verify current API state before retries. Updates are idempotent in outcome.
        new_status={k:val for k,val in current.items() if k in ('madeForKids','selfDeclaredMadeForKids','embeddable','license','publicStatsViewable','containsSyntheticMedia')}
        if i<=2:
            new_status['privacyStatus']='public'
        else:
            new_status['privacyStatus']='private';new_status['publishAt']=scheduled_time
        record['publish_attempted']=True;save(state_file,state)
        yt.videos().update(part='status',body={'id':vid,'status':new_status}).execute()
        refreshed=yt.videos().list(part='status',id=vid).execute().get('items',[])
        if len(refreshed)!=1:raise RuntimeError(f'Part {i}: could not verify updated status.')
        verified=refreshed[0]['status']
        if i<=2:
            if verified.get('privacyStatus')!='public':raise RuntimeError(f'Part {i}: PUBLIC status not confirmed: {verified}')
            record.update(publishing='public',publish_attempted=False)
            print(f'PART {i}: PUBLIC NOW ✓',flush=True)
        else:
            if verified.get('privacyStatus')!='private' or not verified.get('publishAt') or abs((parse(verified['publishAt'])-parse(scheduled_time)).total_seconds())>2:
                raise RuntimeError(f'Part {i}: scheduling not confirmed: {verified}')
            record.update(publishing='scheduled',publish_at=scheduled_time,publish_attempted=False)
            print(f'PART {i}: SCHEDULED {scheduled_time} UTC',flush=True)
        save(state_file,state)
    print('DONE: All 15 uploaded. PARTS 1-2 public; PARTS 3-15 scheduled for public release by YouTube.',flush=True)
    print(f'View saved state: {state_file}',flush=True)

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print('STOPPED: '+str(exc),file=sys.stderr,flush=True);sys.exit(1)
