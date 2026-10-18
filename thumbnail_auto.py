import os
"""Resume-safe ChatGPT -> Flow -> 2K YouTube thumbnail for AncientYouTubeAuto.

No duplicate automatic generation after an uncertain external click. Does not trash other Flow cards.
"""
import hashlib, json, re, sys, time
from pathlib import Path
from PIL import Image, ImageOps
from playwright.sync_api import sync_playwright
from browser_ops import resume_chat, latest_assistant, chat_send, chat_request_visible

STYLE=("Simple educational cartoon, 2D flat illustration, thick black outlines, "
       "stick-figure inspired humans, rounded cartoon characters, minimal shading, "
       "warm color palette, light orange sky, beige ground, clean vector appearance, "
       "educational infographic style, no realism, no 3D, no text")
MODE=os.environ.get('YOUTUBE_MODE','ancient').lower()
MARKER='THUMBNAIL_PROMPT_START'
END='THUMBNAIL_PROMPT_END'

def _save(project, state):
    project.data['thumbnail_auto']=state
    project.save()

def _parse(reply):
    m=re.search(r'THUMBNAIL_PROMPT_START\s*(.*?)\s*THUMBNAIL_PROMPT_END',reply,re.I|re.S)
    if not m: return None
    body=m.group(1).strip().strip('"')
    return body if 100<=len(body)<=4000 else None

def _instruction(project):
    if project.data.get('mode','ancient')=='survival':
        return (f'Write ONE clickable Google Nano Banana 16:9 thumbnail IMAGE prompt for the '
            f'fictional animated survival story "{project.data["title"]}". '
            'Clean hand-drawn 2D educational survival cartoon, flat muted desaturated colors, thin dark charcoal outlines, '
            'TRUE classic stick-figure protagonist with a simple round white head, black dot eyes, straight thin black-line arms and legs, '
            'minimal flat clothing accessories and consistent design, no fleshy limbs or big rounded human bodies, '
            'simple infographic-like clean backgrounds, dramatic survival danger, apartments, city or disaster scene '
            'appropriate to story, one large expressive face, one large instantly recognizable '
            'survival problem or mystery, extremely clean simple mobile-readable composition, '
            'no photorealism and no 3D. Keep a coherent style with the modern story frames. '
            'No words, captions, letters, collage, panels or watermark. '
            'Output ONE standalone detailed IMAGE prompt only between the exact lines '
            'THUMBNAIL_PROMPT_START and THUMBNAIL_PROMPT_END.')
    return (f'For my YouTube documentary titled "{project.data["title"]}", write ONE original, '
        'clickable 16:9 YouTube thumbnail IMAGE prompt for Google Flow Nano Banana. '
        'Make it dramatic, immediately understandable at mobile size and visually different '
        'from ordinary storyboard frames. One large expressive prehistoric character, one '
        'clear survival challenge, compelling composition, saturated warm colors and clean '
        'negative space. Match exactly this channel art style: '+STYLE+'. '
        'No words, captions, letters, or watermarks, no collage, no panels, no photorealism. '
        'Give a single detailed standalone IMAGE prompt, no explanation. '
        'Enclose only the complete prompt between the exact lines THUMBNAIL_PROMPT_START '
        'and THUMBNAIL_PROMPT_END.')

def _image_to_jpeg(download, destination):
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as tmp:
        temp=Path(tmp)/'download'
        download.save_as(str(temp))
        with Image.open(temp) as img:
            img.verify()
        with Image.open(temp) as img:
            w,h=img.size
            if max(w,h)<1000 or w<1000 or h<550:
                raise RuntimeError(f'Thumbnail download only {w}x{h}; expected at least 1K resolution.')
            img=ImageOps.fit(img.convert('RGB'),(1920,1080),method=Image.Resampling.LANCZOS)
            destination.parent.mkdir(parents=True,exist_ok=True)
            tmpjpg=destination.with_suffix('.tmp.jpg')
            for q in (92,86,78,68):
                img.save(tmpjpg,'JPEG',quality=q,optimize=True)
                if tmpjpg.stat().st_size<=1_900_000:break
            if tmpjpg.stat().st_size>2_000_000:
                tmpjpg.unlink(missing_ok=True)
                raise RuntimeError('Thumbnail exceeds YouTube size limit; no thumbnail published.')
            tmpjpg.replace(destination)
    if destination.stat().st_size<15000:
        raise RuntimeError('Thumbnail file too small to trust.')

def ensure_thumbnail(project, context, base):
    destination=project.path('thumbnail/thumbnail.jpg')
    state=project.data.get('thumbnail_auto') or {}
    if destination.is_file() and destination.stat().st_size>15000:
        print(f'THUMBNAIL READY: {destination}',flush=True)
        if state.get('phase')!='done':
            state['phase']='done';_save(project,state)
        return destination
    chat=resume_chat(context,project.data['chat_url'])
    phase=state.get('phase','new')
    if phase=='new':
        request=_instruction(project)
        # Set conservative state before sending. A crash cannot cause duplicate paid work.
        state={'phase':'chat_uncertain','request':request}
        _save(project,state)
        print('THUMBNAIL: asking the existing ChatGPT documentary conversation...',flush=True)
        reply=chat_send(chat,request,marker=END,timeout=300)
        prompt=_parse(reply)
        if not prompt:raise RuntimeError('ChatGPT reply did not contain a complete thumbnail prompt. Review the chat.')
        state.update({'phase':'prompt_ready','prompt':prompt})
        _save(project,state)
    elif phase=='chat_uncertain':
        # Recover a request interrupted while ChatGPT was composing its answer.
        # A saved intent is NOT proof that a message was actually posted.
        request=state.get('request') or _instruction(project)
        posted=chat_request_visible(chat,request)
        if posted is True:
            print('THUMBNAIL: existing ChatGPT request found; recovering its reply without resending.',flush=True)
            deadline=time.monotonic()+300
            prompt=None
            next_notice=0
            while time.monotonic()<deadline:
                prompt=_parse(latest_assistant(chat) or '')
                if prompt:
                    break
                if time.monotonic()>=next_notice:
                    print('THUMBNAIL: waiting for the already-posted ChatGPT response...',flush=True)
                    next_notice=time.monotonic()+25
                time.sleep(3)
            if not prompt:
                raise RuntimeError('Thumbnail request is posted but no complete answer appeared within 300 seconds. '
                                   'Run again to keep waiting; no duplicate request was sent.')
        elif posted is False:
            print('THUMBNAIL: saved intent was not posted; sending one verified request.',flush=True)
            reply=chat_send(chat,request,marker=END,timeout=300)
            prompt=_parse(reply)
            if not prompt:
                raise RuntimeError('Thumbnail ChatGPT response incomplete. Resume to recover it; no blind repeat.')
        else:
            raise RuntimeError('Cannot reliably inspect ChatGPT posted messages. '
                               'Thumbnail request not repeated to prevent duplicates.')
        state.update({'phase':'prompt_ready','prompt':prompt,'request':request})
        _save(project,state)
    if state.get('phase') not in ('prompt_ready','flow_uncertain'):
        raise RuntimeError('Thumbnail state is not ready; refusing an unverified duplicate request.')
    sys.path.insert(0,str(Path(base)/'flow_agent'))
    import agent
    flow=agent.find_flow_page(context)
    flow.bring_to_front()
    if state.get('phase')=='flow_uncertain':
        # The original Generate click might have succeeded. NEVER click it again.
        # Locate only an existing Flow card whose Reuse prompt is exactly ours.
        from batch_agent import gallery_bottom_first, identify_by_reuse
        from types import SimpleNamespace
        visual=SimpleNamespace(number=0,prompt=state['prompt'])
        print('THUMBNAIL: recovering previously generated Flow image; no regeneration.',flush=True)
        deadline=time.monotonic()+90
        candidate=None
        while time.monotonic()<deadline and candidate is None:
            for card in gallery_bottom_first(flow):
                try:
                    result=identify_by_reuse(flow,card,[visual])
                    if result is not None:
                        if candidate is not None and candidate['key']!=card['key']:
                            raise RuntimeError('Multiple thumbnail cards match; cannot choose safely.')
                        candidate=card
                except RuntimeError as exc:
                    if 'Multiple thumbnail' in str(exc):
                        raise
                except Exception:
                    pass
            if candidate is None:
                flow.wait_for_timeout(3000)
        if candidate is None:
            raise RuntimeError('Original thumbnail card not verifiable in Flow after 90 seconds. '
                               'No second thumbnail generated. Inspect the Flow gallery.')
        downloads=[]
        callback=lambda item: downloads.append(item)
        flow.on('download',callback)
        try:
            download,label=agent.download_quality_image(flow,candidate,downloads,0)
        finally:
            flow.remove_listener('download',callback)
        _image_to_jpeg(download,destination)
        state.update({'phase':'done','file':str(destination),'quality':label})
        _save(project,state)
        print(f'THUMBNAIL READY: {destination}',flush=True)
        return destination
    generate=agent.choose_image_mode(flow)
    editor=agent.find_prompt_input(flow)
    prompt=state['prompt']
    flow_prompt=prompt+'\n\n'+agent.SINGLE_SCENE_RULES
    editor.fill(flow_prompt,timeout=12000)
    if not generate.is_enabled():raise RuntimeError('Google Flow Generate button disabled. No thumbnail submitted.')
    before=agent.image_fingerprints(flow)
    # Mark uncertain before pressing Generate to avoid automatic duplicate credit spend.
    state['phase']='flow_uncertain';_save(project,state)
    generate.click(timeout=12000)
    print('THUMBNAIL: submitted to Flow; waiting for the new image...',flush=True)
    candidate=agent.new_image_after_submit(flow,before,agent.IMAGE_TIMEOUT)
    downloads=[]
    callback=lambda item: downloads.append(item)
    flow.on('download',callback)
    try:
        download,label=agent.download_quality_image(flow,candidate,downloads,0)
    finally:
        flow.remove_listener('download',callback)
    _image_to_jpeg(download,destination)
    state.update({'phase':'done','file':str(destination),'quality':label})
    _save(project,state)
    print(f'THUMBNAIL READY: {destination}',flush=True)
    return destination
