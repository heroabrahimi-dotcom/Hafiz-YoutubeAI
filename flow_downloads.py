"""Project-safe image download matching and recovery.

Background Chrome downloads are opaque. Identify files by visual content, not
completion order or filenames, before writing the final visual_### file.
"""
from __future__ import annotations
import re
import shutil
import time
import uuid
from pathlib import Path
from PIL import Image
from image_match import match_image

VALID={'.jpg','.jpeg','.png','.webp'}


def get_previews(page, pending, fingerprint, locate, log):
    refs={}
    for v in pending:
        images=[im for im in fingerprint(page) if im['key']==v['image_key']]
        if len(images)!=1:
            raise RuntimeError(f"VISUAL {v['number']}: matched Flow image card is no longer uniquely visible")
        img=locate(page,images[0])
        img.scroll_into_view_if_needed(timeout=10000)
        # screenshots are temporary local matching references, not final assets
        refs[v['number']]=img.screenshot(timeout=12000, animations='disabled')
    return refs


def _probe(file: Path, min_dim: int):
    if file.stat().st_size<10000:
        return None
    try:
        with Image.open(file) as im:
            im.verify()
        with Image.open(file) as im:
            width,height=im.size
            kind=(im.format or '').upper()
        if max(width,height)<min_dim:
            return None
        suffix={'JPEG':'.jpg','PNG':'.png','WEBP':'.webp'}.get(kind)
        if not suffix:
            return None
        return suffix,width,height
    except Exception:
        return None


def classify(file, refs, pending, download=None):
    """Return visual # by unique media id or confident content comparison."""
    file=Path(file)
    winners=[]
    if download is not None:
        source=((getattr(download,'url','') or '')+' '+(getattr(download,'suggested_filename','') or '')).lower()
        for num, row in pending.items():
            m=re.search(r'/image/([0-9a-f-]{36})',row.get('image_key',''),re.I)
            if m and m.group(1).lower() in source:
                winners.append(num)
    if len(winners)==1:
        return winners[0], 'media-id', []
    if len(winners)>1:
        return None, 'ambiguous-media-id', []
    target, scored=match_image(file,refs)
    if target in pending:
        return target,'visual-content',scored
    return None,'unmatched-content',scored


def consume(download, project, refs, pending, journal, visuals, log, on_unmatched=None):
    """Save safely; ambiguous files remain in recovery, never mislabelled."""
    folder=Path(project)
    recovery=folder.parent/'download_recovery'
    recovery.mkdir(parents=True,exist_ok=True)
    unique=f'flow_{uuid.uuid4().hex}.download'
    tmp=recovery/unique
    try:
        download.save_as(str(tmp))
        failure=download.failure()
        if failure:
            log(f'Flow download failed: {failure}');return None
    except Exception as exc:
        log(f'Flow download save error: {str(exc)[:150]}');return None
    metadata=_probe(tmp,1600)
    if not metadata:
        log('Ignored download: not a valid 2K image.');return None
    suffix,width,height=metadata
    number,how,scored=classify(tmp,refs,pending,download)
    if number is None:
        hint=', '.join(f'{n}:{d:.3f}' for d,n in scored[:3])
        log(f'Unmatched 2K download kept for recovery ({hint or "no visual match"})')
        if on_unmatched is not None:
            on_unmatched(scored)
        return None
    dest=folder/f'visual_{number:03d}{suffix}'
    if dest.is_file() and dest.stat().st_size>=10000:
        log(f'VISUAL {number}: already saved; ignored repeated download.')
        tmp.unlink(missing_ok=True)
        v=visuals[number]
        journal.put(number,v.hash,'completed',file=str(dest),width=width,height=height,
                    quality_ui_label='2K',image_key=pending[number]['image_key'])
        pending.pop(number,None);refs.pop(number,None)
        return number
    if dest.exists():
        log(f'VISUAL {number}: target already exists; keeping recovered copy without overwrite')
        return None
    shutil.move(str(tmp),str(dest))
    v=visuals[number]
    journal.put(number,v.hash,'completed',file=str(dest),width=width,height=height,
                quality_ui_label='2K',image_key=pending[number]['image_key'])
    pending.pop(number,None);refs.pop(number,None)
    log(f'VISUAL {number}: SAVED {dest.name} ({width}x{height}, {how})')
    return number


def recover_recent(folder, refs, pending, journal, visuals, log, lookback=4*3600):
    """Recover known older Chrome downloads without moving/deleting user files."""
    downloads=Path.home()/'Downloads'
    if not downloads.is_dir():return
    checked=0
    for file in sorted(downloads.iterdir(),key=lambda p:p.stat().st_mtime if p.is_file() else 0,reverse=True):
        if not pending or checked>=80:break
        if not file.is_file() or file.suffix.lower() not in VALID:continue
        checked+=1
        if time.time()-file.stat().st_mtime>lookback:break
        if not _probe(file,1600):continue
        number,how,_=classify(file,refs,pending)
        if number is None:continue
        suffix,w,h=_probe(file,1600)
        dest=Path(folder)/f'visual_{number:03d}{suffix}'
        if dest.exists():continue
        shutil.copy2(str(file),str(dest))
        v=visuals[number]
        journal.put(number,v.hash,'completed',file=str(dest),width=w,height=h,quality_ui_label='2K',image_key=pending[number]['image_key'])
        pending.pop(number,None);refs.pop(number,None)
        log(f'VISUAL {number}: RECOVERED verified image from Downloads.')


def recover_quarantine(folder, refs, pending, journal, visuals, log):
    """Recheck earlier unmatched 2K files when the matching set changes.

    The original download is kept intact until it is identified; never infer
    identity from filename, order, or which image was most recently requested.
    """
    recovery = Path(folder).parent / 'download_recovery'
    if not recovery.is_dir() or not pending:
        return
    for file in sorted(recovery.glob('*.download'), key=lambda x: x.stat().st_mtime, reverse=True):
        if not pending:
            break
        metadata = _probe(file, 1600)
        if not metadata:
            continue
        number, how, scores = classify(file, refs, pending)
        if number is None:
            continue
        suffix, width, height = metadata
        dest = Path(folder) / f'visual_{number:03d}{suffix}'
        if dest.exists():
            continue
        # Atomic copy; leave recovery material alone until a completed journal
        # entry exists. This is recoverable after a process crash.
        tmp = dest.with_name(dest.name + '.recovering')
        try:
            shutil.copy2(file, tmp)
            tmp.replace(dest)
            visual = visuals[number]
            journal.put(number, visual.hash, 'completed', file=str(dest),
                        width=width, height=height,
                        quality_ui_label='2K',
                        image_key=pending[number]['image_key'])
            pending.pop(number, None)
            refs.pop(number, None)
            log(f'VISUAL {number}: RECOVERED existing 2K download ({width}x{height}; {how})')
        finally:
            tmp.unlink(missing_ok=True)
