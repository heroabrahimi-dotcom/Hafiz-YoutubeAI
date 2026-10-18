"""Transcript-first narrated video alignment. Requires faster-whisper, Playwright, FFmpeg.

Safety: never invent missing caption/script match; abort before final render unless all
visuals are present and matched with acceptable confidence. Saves reviews for editing.
"""
import argparse, csv, difflib, json, math, re, shutil, subprocess, sys, tempfile
from collections import defaultdict
from pathlib import Path

EXTS={'.jpg','.jpeg','.png','.webp'}
AUDIO={'.mp3','.wav','.m4a','.aac','.flac','.ogg','.opus','.webm'}
URL_DEFAULT='https://chatgpt.com/c/6acc9c04-bbd8-83ec-8d78-55664c49ef58'


def say(s): print(s,flush=True)
def run(cmd, capture=False):
    if capture:
        p=subprocess.run(cmd,capture_output=True,text=True,check=True)
        return p.stdout
    subprocess.run(cmd,check=True)


def get_duration(p):
    return float(run(['ffprobe','-v','error','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1',str(p)],True).strip())


def audios(downloads, a1, a2):
    if a1 and a2: return [Path(a1),Path(a2)]
    candidates=sorted(p for p in downloads.iterdir() if p.is_file() and p.suffix.lower() in AUDIO and 'sully' in p.name.casefold() and '2026-10-12' in p.name)
    if len(candidates)!=2:
        raise RuntimeError('Specify --audio1 and --audio2. Automatic detection needs exactly two 2026-10-12 Sully files.')
    return candidates


def get_images(folder, start, end):
    files={}
    for p in folder.iterdir():
        m=re.fullmatch(r'visual_(\d+)',p.stem,re.I)
        if m and p.suffix.lower() in EXTS:
            n=int(m.group(1))
            if n in files: raise RuntimeError(f'Multiple files for VISUAL {n}')
            files[n]=p
    end=end or max(files,default=0)
    missing=[n for n in range(start,end+1) if n not in files]
    if missing: raise RuntimeError(f'Missing images: {missing}')
    return [(n,files[n]) for n in range(start,end+1)]


def clean_text(s):
    s=re.sub(r'(?m)^\s*#{1,6}\s*','',s)
    s=s.strip().strip('“”"\' ')
    s=re.sub(r'\*\*|__','',s)
    return s.strip()


def extract_scenes(chat_messages):
    # Scans assistant replies throughout the current ChatGPT conversation.
    heading=re.compile(r'(?im)^\s*(?:#{1,6}\s*)?(?:\*\*)?VISUAL\s+(\d+)(?:\*\*)?\s*(?:[—:\-].*)?$')
    script=re.compile(r'(?im)^\s*(?:[-*]\s*)?(?:\*\*)?SCRIPT\s+SENTENCE(?:\*\*)?\s*:\s*(.*)$')
    stop=re.compile(r'(?im)^\s*(?:[-*]\s*)?(?:\*\*)?(?:VISUAL\s+PURPOSE|NANO\s+BANANA\s+PROMPT|GOOGLE\s+NANO\s+BANANA\s+PROMPT|PROMPT)(?:\*\*)?\s*:')
    found={}
    for msg in chat_messages:
        marks=list(heading.finditer(msg))
        for i,m in enumerate(marks):
            n=int(m.group(1)); block=msg[m.end(): marks[i+1].start() if i+1<len(marks) else len(msg)]
            sc=script.search(block)
            if not sc: continue
            tail=block[sc.end():]
            st=stop.search(tail)
            raw=sc.group(1)+'\n'+tail[:st.start() if st else 0]
            raw=clean_text(raw)
            # Script sentence could be multi-line; collapse whitespace.
            raw=re.sub(r'\s+',' ',raw).strip().strip('"“”')
            if len(raw)>12:
                found[n]=raw # if a visual was revised, last version wins
    return found


def collect_chat(url, cdp):
    """Read ChatGPT conversation without assuming one specific DOM layout.

    Supports assistant-role messages, ChatGPT said: h4 headings (the user's
    existing Flow agent), and conversation-turn containers. Does not send text.
    Scrolls older history if the client virtualizes it, preserving each batch.
    """
    from playwright.sync_api import sync_playwright
    import time
    js = r"""() => {
      const clean = s => (s || '').replace(/\u00a0/g, ' ').trim();
      const messages=[];
      const roleNodes=Array.from(document.querySelectorAll('[data-message-author-role="assistant"]'));
      for(const el of roleNodes){
        const t=clean(el.innerText);
        if(t && /\bVISUAL\s+\d+\b/i.test(t)) messages.push(t);
      }
      // Older ChatGPT versions expose only 'ChatGPT said:' headings.
      const headings=Array.from(document.querySelectorAll('h4'));
      for(const h of headings){
        if(clean(h.innerText)!=='ChatGPT said:') continue;
        let node=h.parentElement;
        for(let depth=0;depth<8 && node;depth++,node=node.parentElement){
          const t=clean(node.innerText);
          if(t.startsWith('ChatGPT said:') && /\bVISUAL\s+\d+\b/i.test(t)){
            messages.push(clean(t.replace(/^ChatGPT said:\s*/i,'')));
            break;
          }
        }
      }
      const turns=Array.from(document.querySelectorAll('[data-testid^="conversation-turn-"]'));
      for(const el of turns){
        const t=clean(el.innerText);
        if(t.startsWith('ChatGPT said:') && /\bVISUAL\s+\d+\b/i.test(t))
          messages.push(clean(t.replace(/^ChatGPT said:\s*/i,'')));
      }
      return {messages, diagnostics:{assistant:roleNodes.length,headings:headings.length,turns:turns.length,scrollY:window.scrollY,scrollHeight:document.documentElement.scrollHeight}};
    }"""
    with sync_playwright() as p:
        browser=p.chromium.connect_over_cdp(cdp,timeout=25000)
        try:
            if not browser.contexts:
                raise RuntimeError('No Chrome context. Chrome must be running with remote debugging.')
            pages=[pg for ctx in browser.contexts for pg in ctx.pages if pg.url.split('?')[0] == url.split('?')[0]]
            if len(pages)!=1:
                raise RuntimeError(f'Expected one ChatGPT tab with URL {url}; found {len(pages)}. Keep only one tab for this conversation.')
            page=pages[0]
            found={}
            def capture():
                data=page.evaluate(js)
                for msg in data['messages']:
                    # exact deduplication; keep conflicting revised visual batches as separate entries
                    found.setdefault(msg,msg)
                return data['diagnostics']
            diagnostics=capture()
            # The user's known UI has content but no data-message-author-role;
            # collecting h4 messages works without extra dependencies.
            # Scan backwards to handle lazy-rendered older chat turns.
            previous=(len(found), None)
            stagnation=0
            for _ in range(50):
                page.evaluate("""() => {
                  const nodes=Array.from(document.querySelectorAll('*')).filter(e => {
                    const s=getComputedStyle(e);
                    return e.scrollHeight > e.clientHeight+200 &&
                           /auto|scroll/.test(s.overflowY) && e.clientHeight>250;
                  });
                  const target=nodes.sort((a,b)=>b.scrollHeight-a.scrollHeight)[0];
                  if(target) target.scrollTop=Math.max(0,target.scrollTop-target.clientHeight*2);
                  else window.scrollBy(0,-window.innerHeight*2);
                }""")
                page.wait_for_timeout(250)
                diagnostics=capture()
                # Stop when older messages no longer appear; avoid a long idle wait.
                now=(len(found),diagnostics.get('scrollY'))
                if now==previous: stagnation+=1
                else: stagnation=0
                previous=now
                if stagnation>=4: break
            say(f'Collected {len(found)} unique visual-containing message blocks from ChatGPT (DOM: {diagnostics}).')
            if not found:
                raise RuntimeError('No VISUAL prompts found on this ChatGPT page. Verify the selected conversation and Chrome port 9333.')
            return list(found.values())
        finally:
            browser.close()

def word_tokens(text):
    return re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)?",text.casefold())


def transcribe(parts, model_name, directory):
    try: from faster_whisper import WhisperModel
    except ImportError as e:
        raise RuntimeError('Missing faster-whisper. Run Install-Dependencies.ps1 first.') from e
    cache=directory/'transcript.json'
    if cache.is_file():
        try:
            record=json.loads(cache.read_text(encoding='utf-8-sig'))
            expected=[str(x) for x in parts]
            # Existing transcript was written only after successful transcription.
            # Reuse it only when inputs have not changed since then.
            if (record.get('parts')==expected and record.get('words') and
                all(cache.stat().st_mtime>=part.stat().st_mtime for part in parts)):
                total=sum(get_duration(part) for part in parts)
                say(f'Reusing verified existing word transcript ({len(record["words"])} words); no repeated Whisper run.')
                return record['words'],total
        except Exception:
            pass
    say(f'Loading transcription model {model_name} (CPU int8, first run downloads the model)...')
    model=WhisperModel(model_name,device='cpu',compute_type='int8')
    all_words=[]; offset=0.0; segments_log=[]
    for k,part in enumerate(parts,1):
        say(f'Transcribing Part {k}: {part.name}')
        dur=get_duration(part)
        segs,info=model.transcribe(str(part),language='en',beam_size=5,word_timestamps=True,vad_filter=False)
        for seg in segs:
            segments_log.append({'part':k,'start':offset+seg.start,'end':offset+seg.end,'text':seg.text.strip()})
            for w in seg.words or []:
                toks=word_tokens(w.word)
                for t in toks:
                    all_words.append({'word':t,'start':offset+w.start,'end':offset+w.end})
        offset+=dur
    (directory/'transcript.json').write_text(json.dumps({'parts':[str(x) for x in parts], 'segments':segments_log, 'words':all_words},indent=2),encoding='utf-8')
    (directory/'transcript.txt').write_text('\n'.join(f"[{s['start']:.2f}-{s['end']:.2f}] {s['text']}" for s in segments_log),encoding='utf-8')
    return all_words,offset


def align(scenes, words, minimum=0.70):
    """Chronological, locally bounded alignment, resistant to repeated short phrases.

    IMPORTANT: a high-scoring occurrence hundreds of seconds later is NOT an
    acceptable anchor. Weak/missing lines do not move the cursor; following
    distinctive narration can re-anchor locally. Unresolved lines remain for
    the existing small-gap review mechanism, never silently fabricated.
    """
    trans=[w['word'] for w in words]
    indexes=defaultdict(list)
    for i, token in enumerate(trans):
        indexes[token].append(i)
    results=[]
    last_start=-1
    last_end=-1.0
    for number, sentence in scenes:
        toks=word_tokens(sentence)
        row={'visual':number,'sentence':sentence,'confidence':0.0}
        if len(toks)<2:
            row['reason']='sentence too short to match confidently'
            results.append(row)
            continue
        # Limit local discovery to ~30 seconds after the previous securely
        # matched sentence. This prevents "You look down" at 814 seconds from
        # hijacking an otherwise correct anchor at 170 seconds.
        max_gap=max(32.0, min(60.0, len(toks)*2.5 + 18.0))
        starts=set()
        for j, token in enumerate(toks[:min(8,len(toks))]):
            for occurrence in indexes.get(token,[]):
                candidate=occurrence-j
                if 0<=candidate<len(trans):
                    starts.add(candidate)
        # Fall back to a nearby exhaustive scan if Whisper changed the entire
        # beginning of a sentence. Never scan all later narration as a fallback.
        if last_end >= 0:
            local=[i for i,w in enumerate(words) if
                   last_end-2.0 <= w['start'] <= last_end+max_gap]
            if local:
                starts.update(range(max(0,local[0]-2), min(len(trans),local[-1]+3)))
        elif not starts:
            starts.update(range(min(len(trans),200)))
        best=None
        for start in starts:
            if start <= last_start:
                continue
            moment=words[start]['start']
            if last_end>=0 and (moment < last_end-2.0 or moment>last_end+max_gap):
                continue
            # At the beginning, the first spoken scene should be near t=0.
            if last_end<0 and moment>35:
                continue
            if len(toks)==2:
                if trans[start:start+2]!=toks:
                    continue
                windows=[(1.0,start+1)]
            else:
                windows=[]
                for delta in (-3,-2,-1,0,1,2,3):
                    width=len(toks)+delta
                    if width<2 or start+width>len(trans):
                        continue
                    score=difflib.SequenceMatcher(None,toks,trans[start:start+width],autojunk=False).ratio()
                    windows.append((score,start+width-1))
            for score,last in windows:
                # Prefer a strong local match to an equally good distant one.
                gap=max(0.0,moment-last_end) if last_end>=0 else moment
                rank=score-0.0015*gap
                candidate=(rank,score,-moment,start,last)
                if best is None or candidate>best:
                    best=candidate
        if best is None or best[1]<minimum:
            row['confidence']=round(best[1],3) if best else 0.0
            row['reason']='no confident match near previous spoken sentence; distant matches rejected'
        else:
            _,score,_,start,last=best
            row.update({'confidence':round(score,3),
                        'start':round(words[start]['start'],3),
                        'matched_until':round(words[last]['end'],3)})
            last_start=start
            last_end=words[last]['end']
        results.append(row)
    return results



def independent_anchors(scenes, words, minimum=0.82):
    """Find high-confidence sentences anywhere in audio to recover after an early miss.

    Unique distinctive matches establish real timing anchors; only monotonic
    anchors are accepted. Do not invent timestamps when audio and script differ.
    """
    tokens=[w['word'] for w in words]
    postings=defaultdict(list)
    for idx,tok in enumerate(tokens): postings[tok].append(idx)
    matched=[]
    for number,sentence in scenes:
        ref=word_tokens(sentence)
        if len(ref)<4:
            continue
        # Start candidates from the least frequent long token, not common 'you'.
        keys=sorted(((len(postings[t]),-len(t),j,t) for j,t in enumerate(ref[:min(12,len(ref))]) if t in postings and len(t)>3))
        if not keys: continue
        _,_,offset,token=keys[0]
        candidates=set(p-offset for p in postings[token] if p>=offset)
        best=None
        for start in candidates:
            for delta in (-2,-1,0,1,2):
                width=len(ref)+delta
                if width<3 or start+width>len(tokens): continue
                score=difflib.SequenceMatcher(None,ref,tokens[start:start+width],autojunk=False).ratio()
                if best is None or score>best[0]: best=(score,start)
        if best and best[0]>=minimum:
            matched.append((number,best[1],best[0]))
    # Enforce strictly increasing time and reduce mistaken repeated matches.
    anchors=[]
    last_index=-1
    for number,index,score in matched:
        if index>last_index:
            anchors.append((number,index,score))
            last_index=index
    return anchors


def use_independent_anchors(results, scenes, words, minimum=0.82):
    """Add only verified text matches; never infer missing lines."""
    known={r['visual']:r for r in results}
    anchors=independent_anchors(scenes,words,minimum)
    accepted=0
    for number,index,score in anchors:
        row=known[number]
        if 'start' in row: continue
        row.update(start=round(words[index]['start'],3), confidence=round(score,3),
                   reason='verified independent transcript anchor')
        accepted+=1
    # Reject anchors that violate time ordering, preferring already local matches.
    last=-1.0
    for row in results:
        if 'start' not in row: continue
        if row['start']<=last:
            if row.get('reason')=='verified independent transcript anchor':
                row.pop('start',None)
                row['reason']='conflicting independent anchor; manual review needed'
            else:
                last=row['start']
        else:
            last=row['start']
    return accepted


def estimate_all_timings(results, total):
    """Explicit opt-in fallback; text-length pacing is NOT word alignment."""
    weights=[max(3,len(word_tokens(row['sentence']))) for row in results]
    weight_sum=sum(weights)
    running=0
    for row,w in zip(results,weights):
        row['start']=round(total*running/weight_sum,3)
        row['estimated']=True
        row['confidence']=0.0
        row['reason']='APPROXIMATE: weighted scene duration, NOT transcript matched'
        running+=w


def recover_small_alignment_gaps(results, total, max_gap=2):
    """Estimate a *small* missing run between confidently transcribed anchors.

    No guess is made for beginning/end gaps, large gaps, nonmonotonic anchors,
    or implausibly large/small time intervals. Every estimate is marked in the
    CSV audit and requires human spot-checking when editing is important.
    """
    recovered=[]
    i=0
    while i<len(results):
        if 'start' in results[i]:
            i+=1
            continue
        j=i
        while j<len(results) and 'start' not in results[j]:
            j+=1
        count=j-i
        if count>max_gap or i==0 or j==len(results):
            i=j
            continue
        left=results[i-1]
        right=results[j]
        t0=float(left['start'])
        t1=float(right['start'])
        span=t1-t0
        # One preceding sentence plus the missing ones must fit between anchors.
        if not (0<=t0<t1<=total+0.5) or span < (count+1)*0.35 or span > (count+1)*35:
            i=j
            continue
        weights=[max(2,len(word_tokens(r['sentence']))) for r in results[i-1:j]]
        denom=sum(weights)
        cumulative=weights[0]
        proposed=[]
        for offset in range(count):
            proposed.append(t0+span*cumulative/denom)
            cumulative+=weights[offset+1]
        times=[t0]+proposed+[t1]
        if any(b-a<0.25 for a,b in zip(times,times[1:])):
            i=j
            continue
        for pos,ts in zip(range(i,j),proposed):
            results[pos]['start']=round(ts,3)
            results[pos]['estimated']=True
            results[pos]['reason']='ESTIMATED: interpolated between matched neighbours; review timing'
            recovered.append(results[pos]['visual'])
        i=j
    return recovered


def audit_results(results, folder):
    (folder/'matches.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
    with (folder/'timings_review.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=['visual','start','end','confidence','estimated','sentence','reason'])
        w.writeheader()
        for r in results:
            w.writerow({k:r.get(k,'') for k in w.fieldnames})


def compute_segments(results, total):
    if any('start' not in r for r in results):
        raise RuntimeError('Some visual sentences could not be aligned. See timings_review.csv and transcript.txt. Rendering was stopped to avoid wrong images.')
    starts=[r['start'] for r in results]
    # Repeated script sentence assigned to multiple pictures: split their interval equally.
    i=0
    while i<len(starts):
        j=i+1
        while j<len(starts) and starts[j] <= starts[i]+0.10:j+=1
        if j>i+1:
            next_start=starts[j] if j<len(starts) else total
            if next_start-starts[i]< (j-i)*0.15: raise RuntimeError('Repeated scene timestamps too close to split safely.')
            for k in range(i+1,j):starts[k]=starts[i]+(next_start-starts[i])*(k-i)/(j-i)
        i=j
    cuts=[0.0]+[float(x) for x in starts[1:]]+[total]
    if any(cuts[i+1]-cuts[i]<0.12 for i in range(len(cuts)-1)):
        raise RuntimeError('Invalid or too-short cut detected. Review the matched timings.')
    for i,r in enumerate(results):r['start']=round(cuts[i],3);r['end']=round(cuts[i+1],3)
    return cuts


def quote_concat(p):
    return str(p.resolve()).replace('\\','/').replace("'", "'\\''")


def render(items, parts, cuts, output, fps, crf):
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='script_synced_') as td:
        td=Path(td)
        listing=td/'slides.ffconcat'
        with listing.open('w',encoding='utf-8') as f:
            f.write('ffconcat version 1.0\n')
            for (_,image),a,b in zip(items,cuts,cuts[1:]):
                f.write(f"file '{quote_concat(image)}'\n")
                f.write(f'duration {b-a:.9f}\n')
            f.write(f"file '{quote_concat(items[-1][1])}'\n")
        a1,a2=parts; merged=td/'narration.m4a'
        say('Combining narration audio...')
        run(['ffmpeg','-hide_banner','-nostdin','-y','-i',str(a1),'-i',str(a2),'-filter_complex','[0:a:0]aresample=async=1:first_pts=0[a0];[1:a:0]aresample=async=1:first_pts=0[a1];[a0][a1]concat=n=2:v=0:a=1[out]','-map','[out]','-c:a','aac','-b:a','192k',str(merged)])
        say('Rendering scene-synchronized video...')
        run(['ffmpeg','-hide_banner','-nostdin','-y','-safe','0','-f','concat','-i',str(listing),'-i',str(merged),'-filter_complex',f'[0:v]fps={fps},scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p[v]','-map','[v]','-map','1:a:0','-t',f'{cuts[-1]:.3f}','-c:v','libx264','-preset','veryfast','-crf',str(crf),'-c:a','copy','-movflags','+faststart',str(output)])
    say(f'FINISHED: {output}')


def main():
    ap=argparse.ArgumentParser(description='Transcribe audio, align every visual with original script sentence, render only if matches pass.')
    ap.add_argument('--audio1',type=Path);ap.add_argument('--audio2',type=Path)
    ap.add_argument('--downloads',type=Path,default=Path.home()/'Downloads')
    ap.add_argument('--images',type=Path,default=Path(r'C:\YouTubeAI\images'))
    ap.add_argument('--chat-url',default=URL_DEFAULT)
    ap.add_argument('--cdp',default='http://127.0.0.1:9333')
    ap.add_argument('--scene-map',type=Path,help='Optional scene-map JSON mapping visual numbers to script sentences (no ChatGPT browser required)')
    ap.add_argument('--work',type=Path,default=Path(r'C:\YouTubeAI\AudioScriptSync'))
    ap.add_argument('--output',type=Path,default=Path(r'C:\YouTubeAI\final_video_script_synced.mp4'))
    ap.add_argument('--model',default='small',choices=['tiny','base','small','medium'])
    ap.add_argument('--min-confidence',type=float,default=0.70)
    ap.add_argument('--start',type=int,default=1);ap.add_argument('--end',type=int)
    ap.add_argument('--fps',type=int,default=30);ap.add_argument('--crf',type=int,default=20)
    ap.add_argument('--audit-only',action='store_true',help='Save transcript and alignment review but do not render video')
    ap.add_argument('--allow-approximate',action='store_true',help='OPTIONAL: render with estimated scene cuts if script does not match narration; timing will NOT be exact')
    args=ap.parse_args()
    for cmd in ('ffmpeg','ffprobe'):
        if not shutil.which(cmd):raise RuntimeError(f'{cmd} not found. Install FFmpeg and reopen terminal.')
    args.work.mkdir(parents=True,exist_ok=True)
    parts=audios(args.downloads,args.audio1,args.audio2)
    items=get_images(args.images,args.start,args.end)
    say(f'Found {len(items)} numbered images ({items[0][0]}–{items[-1][0]}).')
    if args.scene_map:
        src=json.loads(args.scene_map.read_text(encoding='utf-8-sig'))
        sentences={int(n):text for n,text in src.items()}
    else:
        from recover_history import recover
        msgs=recover(args.chat_url,args.cdp)
        sentences=extract_scenes(msgs)
        # Incorporate earlier successfully recovered scene records from previous runs.
        prior=args.work/'scene_sentences.json'
        if prior.exists():
            old=json.loads(prior.read_text(encoding='utf-8-sig'))
            for k,v in old.items():
                if v and int(k) not in sentences: sentences[int(k)]=v
        say(f'Recovered SCRIPT SENTENCE fields for {len(sentences)} visuals.')
    (args.work/'scene_sentences.json').write_text(json.dumps(sentences,indent=2),encoding='utf-8')
    absent=[n for n,_ in items if n not in sentences]
    if absent:
        raise RuntimeError(f'Missing SCRIPT SENTENCE fields for VISUALS {absent} from ChatGPT browser. Saved scene_sentences.json. Cannot truthfully match these images to audio. Supply --scene-map after retrieving the missing prompts.')
    scenes=[(n,sentences[n]) for n,_ in items]
    words,total=transcribe(parts,args.model,args.work)
    say(f'Transcribed {len(words)} spoken words across {total:.1f}s.')
    results=align(scenes,words,args.min_confidence)
    initial=sum('start' in r for r in results)
    if initial<len(results):
        recovered=use_independent_anchors(results,scenes,words,max(0.82,args.min_confidence))
        say(f'Initial matches: {initial}/{len(results)}; independent transcript anchors recovered: {recovered}.')
    estimated=recover_small_alignment_gaps(results,total)
    missing_before=[r['visual'] for r in results if 'start' not in r]
    if missing_before and args.allow_approximate:
        say('WARNING: script/audio mismatch or large alignment gaps. Using approximate sentence-length timing BY EXPLICIT REQUEST.')
        estimate_all_timings(results,total)
    audit_results(results,args.work)
    if estimated:
        say(f'WARNING: estimated timing for visuals {estimated} using neighbouring matches. Review timings_review.csv if precise cuts matter.')
    missing=[r['visual'] for r in results if 'start' not in r]
    if missing:
        raise RuntimeError(f'Could not safely align {len(missing)}/{len(results)} visuals (first 12: {missing[:12]}). This usually means the scene-map script differs from part_1.wav + part_2.wav. Review transcript.txt and scene_sentences.json in {args.work}. To make an approximate preview using the ORIGINAL two-part audio, rerun this script with --allow-approximate. No video was rendered.')
    cuts=compute_segments(results,total)
    audit_results(results,args.work)
    say(f'Prepared timings for {len(results)} visuals. Estimated: {sum(bool(r.get("estimated")) for r in results)}. Review saved in {args.work}.')
    if args.audit_only:
        say('Audit-only mode: video render skipped.');return
    render(items,parts,cuts,args.output,args.fps,args.crf)

if __name__=='__main__':
    try:main()
    except Exception as e:
        print(f'ERROR: {e}',file=sys.stderr);sys.exit(1)
