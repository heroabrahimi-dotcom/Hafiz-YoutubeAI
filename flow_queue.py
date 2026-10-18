"""One 2K request and one verified image download at a time.

Identity follows exact Reuse-prompt-verified Flow card and an isolated download
request, not perceptual similarity between screenshots and upscaled files.
"""
from __future__ import annotations
import re
import time
import uuid
import shutil
from pathlib import Path
from flow_downloads import _probe


def collect_2k_batch(page, *, pending, references, journal, visuals,
                     output_dir, fingerprint, find_card, recover_card,
                     start_download, log, snapshot,
                     retries=3, retry_after=120, heartbeat=30):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True,exist_ok=True)
    quarantine = output_dir.parent/'download_recovery'
    quarantine.mkdir(parents=True,exist_ok=True)
    events=[]
    def on_download(d): events.append(d)
    page.on('download',on_download)
    try:
        for number in sorted(list(pending)):
            if number not in pending: continue
            key=pending[number]['image_key']
            media_match=re.search(r'/image/([0-9a-f-]{36})',key or '',re.I)
            media_id=media_match.group(1).lower() if media_match else ''
            done=False
            for attempt in range(1,retries+1):
                # Drain preexisting delayed transfers before requesting the next.
                # Old requests are never blindly assigned to this visual.
                if events:
                    log(f'VISUAL {number}: isolating {len(events)} older download(s)')
                events.clear()
                page.wait_for_timeout(2500)
                events.clear()
                card=find_card(fingerprint(page),key)
                if card is None:
                    card=recover_card(number,visuals[number])
                if card is None:
                    raise RuntimeError(f'VISUAL {number}: original-prompt-verified card missing')
                key=card['key']
                pending[number]['image_key']=key
                log(f'VISUAL {number}: isolated 2K download {attempt}/{retries}')
                start_download(page,card,number)
                deadline=time.monotonic()+retry_after
                last_report=0
                while time.monotonic()<deadline:
                    if not events:
                        if time.monotonic()-last_report>=heartbeat:
                            log(f'VISUAL {number}: waiting for this card\'s 2K image')
                            last_report=time.monotonic()
                        page.wait_for_timeout(900)
                        continue
                    download=events.pop(0)
                    src=((getattr(download,'url','') or '')+' '+(getattr(download,'suggested_filename','') or '')).lower()
                    url_ids=re.findall(r'/image/([0-9a-f-]{36})',src,re.I)
                    if url_ids and media_id and media_id not in url_ids:
                        log(f'VISUAL {number}: delayed download belongs to another Flow card; ignoring')
                        continue
                    tmp=quarantine/f'isolated_{uuid.uuid4().hex}.download'
                    try:
                        download.save_as(str(tmp))
                        if download.failure():
                            log(f'VISUAL {number}: download failed; retrying');continue
                        metadata=_probe(tmp,1600)
                        if not metadata:
                            log(f'VISUAL {number}: non-2K download ignored');continue
                        suffix,w,h=metadata
                        dest=output_dir/f'visual_{number:03d}{suffix}'
                        if dest.is_file() and dest.stat().st_size>=10000:
                            log(f'VISUAL {number}: verified file already exists')
                        else:
                            shutil.move(str(tmp),str(dest))
                        journal.put(number,visuals[number].hash,'completed',file=str(dest),
                                    width=w,height=h,quality_ui_label='2K',image_key=key)
                        pending.pop(number,None)
                        references.pop(number,None)
                        log(f'VISUAL {number}: SAVED {dest.name} ({w}x{h}; isolated card download)')
                        done=True
                        break
                    finally:
                        tmp.unlink(missing_ok=True)
                if done: break
                log(f'VISUAL {number}: download not received; retrying same prompt-verified card')
            if not done:
                snapshot(page,f'visual_{number}_isolated_2k_timeout')
                raise RuntimeError(f'VISUAL {number}: no 2K download after {retries} isolated requests; saved progress preserved')
        log('ALL 2K FILES SAVED; no screenshot-similarity guessing.')
    finally:
        page.remove_listener('download',on_download)
