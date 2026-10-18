"""Resumable ChatGPT -> AI Studio Enzo -> Google Flow -> synced MP4 pipeline.

Never repeats a possibly charged audio/image request without journal checks.
Google/ChatGPT UI selectors can change; stop with diagnostics rather than guessing.
"""
from __future__ import annotations
import os
import argparse, datetime as dt, hashlib, json, os, re, shutil, subprocess, sys, time, threading, queue
from difflib import SequenceMatcher
from pathlib import Path
from browser_ops import (AI_STUDIO_URL, STYLE, UIBlocked, snapshot, log, chat_send,
                         chat_url, new_chat, resume_chat, latest_assistant, recent_assistants, studio_make_part, attach_guide, chat_request_visible)

BASE=Path(__file__).resolve().parent
PROJROOT=Path(os.environ.get('YOUTUBE_PROJECT_ROOT',r'C:\YouTubeAI\Projects'))
CONTROL=PROJROOT/'_AncientYouTubeAuto'
FLOW=BASE/'flow_agent'
SYNC=BASE/'sync'/'sync_video.py'
VOICE_EXTS=('.wav','.mp3','.m4a','.ogg','.webm')
DOC_STYLE=('Simple educational cartoon, 2D flat illustration, thick black outlines, '
 'stick-figure inspired humans, rounded cartoon characters, minimal shading, warm color palette, '
 'light orange sky background, beige ground, clean vector appearance, educational infographic '
 'style, consistent character design, high readability, no realism, no photorealism, '
 'no cinematic lighting, no 3D render, no text.')
SEED_USED=['How Did Ancient Humans Survive Without Doctors?']
MODE=os.environ.get('YOUTUBE_MODE','ancient').lower()
if MODE not in ('ancient','survival'):
    raise RuntimeError('YOUTUBE_MODE must be ancient or survival')
SURVIVAL_STYLE=(
 'Clean hand-drawn 2D educational survival-story cartoon, exactly like a minimalist underground bunker cross-section explainer. '
 'TRUE CLASSIC STICK-FIGURE PEOPLE ONLY: perfectly round simple off-white head, tiny black dot eyes, short simple mouth, '
 'one narrow black line for the torso, very thin black stick arms and stick legs, tiny simplified hands and feet. '
 'No normal human anatomy, no muscular or bulky bodies, no realistic faces, no Disney/Pixar/anime character proportions. '
 'When clothing matters, show it as minimal simple flat accessories on the same recognizable stick person, without turning '
 'arms or legs into fleshy limbs. Keep each recurring character distinctive and consistent by head shape, hat, accessories, '
 'facial details and simple clothing colors; do not change that design across visuals. '
 'Flat straight-on 2D compositions with clean dark charcoal slightly hand-drawn outlines of consistent medium-thin weight; '
 'minimal clean shading, large flat simple color areas, pale off-white or white sky, muted desaturated taupe-gray, sage green, '
 'warm gray, beige, dusty blue and brown. Clean explanatory cutaway rooms, diagrams, surface and underground cross-sections '
 'whenever the exact story sentence calls for them; urban/subway/city locations should retain their correct modern setting '
 'rather than always show a bunker. Clearly readable detailed props like pipes, ladders, shelves, tables, radios and supplies '
 'only when story-relevant. Static illustrated animation frames, no photorealism, no 3D, no textured painted graphic-novel art, '
 'no hyperrealistic lighting, no excessive hatching, no cinematic color grading. Vary shot size and visual contents as required '
 'by narration, including object-only, empty environments, POV shots, maps and infographics: do NOT add a character when one '
 'is not appropriate. Exactly 16:9, no text or letters unless grounded by source sentence.'
)
if MODE=='survival':
    DOC_STYLE=SURVIVAL_STYLE
    SEED_USED=[]



def readjson(path, default=None):
    p=Path(path)
    return json.loads(p.read_text(encoding='utf-8-sig')) if p.exists() else default


def atomic(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    os.replace(tmp,path)


def save_text(path,s):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(s.strip()+'\n',encoding='utf-8')


def norm(s):return re.sub(r'[^a-z0-9]+','',s.casefold())


def slug(s):
    v=re.sub(r'[^a-zA-Z0-9]+','-',s).strip('-').lower()
    return v[:65] or 'untitled'


def single_line(s):return re.sub(r'\s+',' ',s).strip()


def used_titles():
    titles=set(map(norm,SEED_USED)); names=list(SEED_USED)
    history=readjson(CONTROL/'history.json',[]) or []
    for r in history:
        if r.get('title'):
            titles.add(norm(r['title']));names.append(r['title'])
    if PROJROOT.exists():
        for p in PROJROOT.iterdir():
            if not p.is_dir():continue
            m=readjson(p/'project.json',{}) or {}
            if m.get('title'):
                titles.add(norm(m['title']));names.append(m['title'])
            # Old local project had a folder but no metadata: protect its topic too.
            if 'ancienthumansnodoctors' in norm(p.name):titles.add(norm(SEED_USED[0]))
    return titles,list(dict.fromkeys(names))


def topic_prompt(names):
    if MODE=='survival':
        return ("Act as a viral YouTube strategist for animated FICTIONAL modern-day survival stories. "
          "Consider zombie outbreaks, isolated apartments, disaster survival, building lockdowns, "
          "failed infrastructure, countdowns, extreme weather, mysterious emergency alerts, "
          "impossible escape dilemmas and resource shortages. Pick five original, surprising "
          "8-12 minute story concepts and SELECT THE SINGLE BEST unused title. "
          "Use clear specific high-stakes titles, not ancient-history documentaries. "
          "Prefer a concrete protagonist, location, timer or survival obstacle. "
          "Evaluate fresh storytelling, plausibility within the fictional premise, click appeal, "
          "thumbnail potential and character stakes. Do not repeat or paraphrase these titles:\n"
          + '\n'.join('- '+x for x in names)+"\n"
          "Return EXACTLY this machine-readable format, no markdown fences:\n"
          "SELECTED_TITLE: <specific original modern survival story title>\n"
          "SCORE: 1-10\n"
          "WHY: One sentence explaining the survival hook and click appeal.\n"
          "ALTERNATIVES: four distinct unused titles separated by |\n"
          "END_TOPIC_SELECTION")
    return ("Act as a viral YouTube strategist specializing in prehistoric human history and ancient survival content. "
      "Consider ancient humans, evolution, Ice Age survival, hunter-gatherers, lost technologies, "
      "communication, adaptation, strange history. Generate 5 surprising, clear, curiosity-driven "
      "topics suitable for 8-12 minute documentaries; rate them internally for factual support, fresh "
      "storytelling, click appeal and thumbnail potential, and PICK ONLY ONE best unused topic.\n"
      "Do not repeat or just paraphrase any used topics:\n"+'\n'.join('- '+x for x in names)+"\n"
      "Reply strictly in this machine-readable format, no markdown fences:\n"
      "SELECTED_TITLE: How Did ...?\n"
      "SCORE: 1-10\n"
      "WHY: A single sentence about the hook and archaeological/evolutionary evidence.\n"
      "ALTERNATIVES: four unused titles separated by |\n"
      "END_TOPIC_SELECTION")


def parse_topic(reply,used):
    m=re.search(r'(?im)^SELECTED_TITLE\s*:\s*(.+?)\s*$',reply)
    if not m:raise UIBlocked('ChatGPT did not provide SELECTED_TITLE; inspect reply. Do not guess a topic.')
    title=m.group(1).strip().strip('"â€œâ€')
    if len(title)<16 or len(title)>125 or '...' in title or any(SequenceMatcher(None,norm(title),old).ratio()>0.90 for old in used):
        raise UIBlocked(f'Chosen topic is invalid or was already used: {title!r}. No new project created.')
    return title


def select_unused_topic(chat, names, used, max_requests=5):
    """Pick from the selected candidate/alternatives; retry safely if all fail.

    Only submits another request after a completed ChatGPT response was received.
    Never makes a project until a candidate passes the existing duplicate checks.
    """
    rejected = []
    checked = set(used)
    for attempt in range(1, max_requests + 1):
        if attempt == 1:
            prompt = topic_prompt(names) + '\nUse the attached Prompts.pdf as the primary workflow/style guide.'
        else:
            prompt = (
                'Your previous completed topic-selection response offered no acceptable unused title. '
                + ('Generate FIVE new modern animated fictional survival-story ideas. ' if MODE=='survival' else 'Generate FIVE different prehistoric-history documentary ideas. ')
                + 'Do not repeat or paraphrase any previously used or rejected topics. '
                'Reject titles already in the list before replying.\n'
                'PREVIOUSLY USED OR REJECTED TITLES:\n'
                + '\n'.join('- ' + x for x in list(dict.fromkeys(names + rejected)))
                + '\nReply in EXACTLY this format, with no markdown fences:\n'
                + ('SELECTED_TITLE: <original survival story title>\nSCORE: 1-10\n' if MODE=='survival' else 'SELECTED_TITLE: How Did ...?\nSCORE: 1-10\n')
                + ('WHY: One sentence about the survival hook and stakes.\n' if MODE=='survival' else 'WHY: One sentence about the evidence and hook.\n')
                + 'ALTERNATIVES: four other unused titles separated by |\nEND_TOPIC_SELECTION'
            )
        log(f'Topic selection request {attempt}/{max_requests}.')
        reply = chat_send(chat, prompt, 'END_TOPIC_SELECTION')
        selected = re.search(r'(?im)^SELECTED_TITLE\s*:\s*(.+?)\s*$', reply)
        alt = re.search(r'(?im)^ALTERNATIVES\s*:\s*(.+?)\s*$', reply)
        candidates = []
        if selected:
            candidates.append(selected.group(1))
        if alt:
            candidates.extend(alt.group(1).split('|'))
        if not candidates:
            log(f'Topic selection request {attempt}: no parseable candidates; requesting a corrected response.')
            continue
        for candidate in candidates:
            title = candidate.strip().strip('"â€œâ€\' ').strip()
            canonical = norm(title)
            if not canonical or canonical in checked:
                continue
            checked.add(canonical)
            rejected.append(title)
            try:
                approved = parse_topic('SELECTED_TITLE: ' + title, used)
            except UIBlocked as error:
                log(f'Rejected title {title!r}: {error}')
                continue
            # Also guard against near-duplicates among candidates we have rejected.
            if any(SequenceMatcher(None, canonical, norm(other)).ratio() > 0.90
                   for other in rejected[:-1]):
                log(f'Rejected near-duplicate candidate {title!r}.')
                continue
            if selected and title == selected.group(1).strip().strip('"â€œâ€\' ').strip():
                log(f'Accepted selected topic: {title}')
            else:
                log(f'Accepted unused alternative: {title}')
            return approved
        log(f'Topic selection request {attempt}: selected title and alternatives all invalid/used.')
    raise UIBlocked(f'No valid unused topic after {max_requests} completed topic-selection replies. '
                    'No new project created; check used-topic history and topic rules.')


def script_request(topic,part,previous=''):
    # SURVIVAL_STORYCRAFT_V1 â€” studied reference scripts; applies ONLY to Survival mode.
    if MODE=='survival':
        craft=(
          "You are a high-retention fictional survival-thriller narrator writing for a 2D stick-figure "
          "animated YouTube channel. Write ONLY the spoken narration for the original premise "
          f"{topic!r}. Use an immersive SECOND-PERSON point of view ('you'), simple natural "
          "American English, mostly short punchy sentences with occasional longer explanatory ones. "
          "Tell one coherent original story, never reproduce any characters, locations, devices, "
          "named rules, plot turns or signature dialogue from existing YouTube scripts. "
          "OPEN IN MOTION: in the first 1-3 sentences establish a precise physical location, "
          "an observable danger, an immediate forced choice and a ticking consequence. "
          "Do NOT start with a definition, generic apocalypse summary, backstory, or 'imagine'. "
          "Within roughly 20 seconds the viewer must know what can kill or trap the protagonist. "
          "Use a short, sharp early foreshadow of a specific unresolved object, sound, person "
          "or choice that will matter later, WITHOUT giving away its answer. "
          "SURVIVAL ENGINE: Create an internally consistent chain of story-specific practical "
          "problems (for example limited air, damaged exits, electricity, contaminated water, "
          "food, heat, smoke, weather, noise, equipment, injury or trust â€” ONLY if relevant). "
          "Show the viewer the available resources, geography, hazards and rules before a plan "
          "depends on them. Give each important choice an immediate measurable benefit AND "
          "a nontrivial later cost, risk, tradeoff, or new problem. Never solve trouble by "
          "random unintroduced tools, impossible engineering or lucky rescue. "
          "REALISM: Respect time, distance, human fatigue, accessible tools, engineering, "
          "mechanical direction of doors, ventilation, emergency systems, resource depletion "
          "and the limits of what the protagonist knows. Make estimates proportional and check "
          "arithmetic internally; do not invent precision or statistics for decoration. "
          "Treat hazardous survival tactics responsibly: show uncertainty and limitations, "
          "do not portray improvised unsafe actions as guaranteed safe. Separate invented "
          "disaster/zombie rules from genuine real-world physics and medicine. When a premise "
          "requires speculative mechanics, establish them explicitly and obey them. "
          "SUSPENSE RHYTHM: Every 30-60 seconds reveal a meaningful observation, reversal, "
          "discovery, time loss, human consequence or narrowing option. Vary intensity: "
          "tense action, brief quiet, concrete calculation, difficult decision, new threat. "
          "Occasionally end a mini-sequence on a short stand-alone line to create a beat; "
          "avoid nonstop breathless cliffhangers, empty questions, melodrama and clickbait. "
          "Use recurring sensory signals and physical objects as plot anchors. Plant details "
          "early, pay most off causally later, and allow at least one satisfying realization "
          "that reinterprets something previously observed. "
          "EMOTION: Give the protagonist a specific personal stake or relationship, show "
          "small human gestures and consequential moral decisions without long speeches. "
          "When other people appear, distinguish them through one memorable grounded trait "
          "and make their actions change the survival math or available options. "
          "CONTINUITY: Keep character identities, location layout, timelines, inventory, "
          "danger mechanics, injuries, promises and decisions consistent across BOTH parts. "
          "Time jumps or countdown updates must be explicit and earned. The timeline must "
          "fit the original title. Avoid generic day-by-day lists; each section must "
          "meaningfully change the situation. Make each narration sentence depictable by "
          "one clear visual when requested later. "
          "VOICE: No screenplay dialogue labels, no '[screaming]', no sound cues, no stage "
          "directions, no headings, no bullet points, no citations, no explanations outside "
          "script boundaries. Avoid 'stay until the end', like-and-subscribe appeals, "
          "comment requests and repetitive 'new problem/second problem' phrasing. "
          "Write 700-900 spoken words PER PART; exactly 2 parts. "
          "Each complete sentence must be on its OWN LINE with no blank lines. "
          "Do not break sentences across lines or combine multiple complete sentences on one line. "
        )
        if part==1:
            return (craft +
              "WRITE PART 1 OF 2. Build a specific danger-to-decision chain and introduce "
              "at least one human dilemma. Escalate to a materially new discovery or setback "
              "at the very end, unresolved but natural, so Part 2 can continue immediately. "
              "No summary and no premature resolution. Keep all special facts and numbers "
              "consistent for continuation. Output exactly:\n"
              "SCRIPT_PART_1_START\n<spoken narration only>\nSCRIPT_PART_1_END")
        return (craft +
          "WRITE PART 2 OF 2. Continue immediately after the quoted Part 1 ending, "
          "without recap or repetition. Do not undo the earlier constraints. Make the "
          "main turning point depend on a previously established rule, overlooked detail "
          "or meaningful sacrifice; pay off the early foreshadow and resolve the main "
          "survival goal with a plausible consequence. End with an emotionally resonant "
          "last sentence, optionally one restrained final revelation that is consistent "
          "with established events, not a random new cliffhanger. "
          "The LAST PART-1 WORDS are provided for continuity only; do not quote or "
          "reproduce them:\n" + previous[-1600:] +
          "\nOutput exactly:\nSCRIPT_PART_2_START\n<spoken narration only>\nSCRIPT_PART_2_END")
    intro=("You are a precise YouTube documentary narrator/writer. Write only spoken narration about "
      f"{topic!r}. The channel covers archaeology, anthropology, ancient survival and human evolution. "
      "Follow this structure: dramatic evidence-based hook; big mystery; investigation; escalating discoveries; "
      "a real twist; clear reveal; human implication. Start with a specific mystery or archaeological fact, "
      "not a definition. Use compelling, conversational plain English and factual nuance. No invented findings. "
      "No headings, no bullet points, no stage directions, no planning commentary, no citations inside narration. "
      "Keep one curiosity gap roughly every 30-60 seconds. Facts should connect to consequences and survival. "
      "Write 700-900 words per part for a roughly 10-12 minute total narration. "
	"FORMATTING RULE: Put every sentence on its own line. "
	"Do not leave any blank lines between sentences or paragraphs. "
	"Use exactly one newline between sentences. ")
    if part==1:
        return (intro+"Write PART 1 of 2. Finish naturally but do not conclude; Part 2 will continue. "
        "Use these exact boundary lines, outside the script:\nSCRIPT_PART_1_START\n<spoken narration only>\nSCRIPT_PART_1_END")
    return (intro+"Write PART 2 of 2. Continue directly from Part 1, avoid recap and repeated sentences, "
      "bring the mystery to a conclusion. Last sentence must sound like an ending. "
      "The previous part ends with these words (do not repeat them):\n"+previous[-1300:]+"\n"
      "Use these exact boundary lines, outside the script:\nSCRIPT_PART_2_START\n<spoken narration only>\nSCRIPT_PART_2_END")


def parse_script(s,num):
    start=f'SCRIPT_PART_{num}_START';end=f'SCRIPT_PART_{num}_END'
    if s.count(start)!=1 or s.count(end)!=1:raise UIBlocked(f'Missing/duplicated Part {num} boundary. No guessing which text to narrate.')
    t=s.split(start,1)[1].split(end,1)[0].strip()
    t=re.sub(r'(?im)^\s*(?:Writing|Copy|Part [12]|Narration)\s*$', '', t).strip()
    count=len(t.split())
    if count<450 or count>1200:raise UIBlocked(f'Part {num} has {count} words; expected 450-1200. Review ChatGPT output.')
    return t


def split_sentences(script):
    # One scene per sentence, ensuring no narration sentence is silently skipped.
    t=re.sub(r'\s+',' ',script).strip()
    parts=re.split(r'(?<=[.!?])\s+(?=[â€œ"\'A-Z0-9])',t)
    return [x.strip() for x in parts if x.strip()]


# SURVIVAL_DETAILED_PROMPT_V1: long prompts, richer reference-consistent quality.
SURVIVAL_DETAILED_GUIDANCE = (
    'LENGTH REQUIRED: The NANO BANANA PROMPT field for EACH VISUAL must contain 2,000 to 4,000 '
    'characters, including spaces (aim for 2,800 to 3,400). This limit applies individually '
    'to the prompt field, not the script sentence, title, or whole batch. '
    'Keep it one coherent, standalone paragraph, with meaningful concrete details, not padding or repetition. '
    'VISUAL EXACTNESS: Begin with the one exact action, physical situation, or illustrative claim in the '
    'supplied sentence. Do not invent events, people, possessions, statistics, labels, injuries, geography, '
    'or timeline details beyond established continuity and the supplied sentence. '
    'REFERENCE-QUALITY ART DIRECTION: emulate the user-approved clean 2D hand-drawn underground-survival '
    'cutaway reference, NOT its specific bunker contents when unrelated. Use precise dark charcoal ink contours, '
    'carefully aligned thin-to-medium line weight, clean object silhouettes, controlled micro-detail '
    'on relevant architecture and machinery, restrained surface wear, legible layered depth, deliberately '
    'organized shapes and professional clean edges. Muted slate, dusty blue, taupe-gray, pale sage, '
    'warm beige, desaturated brown and off-white with consistent soft practical illumination. '
    'Genuinely excellent high-resolution craftsmanship, fine edge definition, coherent geometry, '
    'plausible joints and hardware, clean perspective, no random strokes, no blur, no blocky pixelation, '
    'no greasy shading, no excessive textures, no photorealism, no 3D, no watermarks. '
    'CHARACTER REQUIREMENT: Every human, when present, is a true thin stick man or stick woman with '
    'small simple round off-white head, tiny eyes, simple mouth, single narrow black-line torso and '
    'very thin black-line arms and legs. Clothing is simplified as small accessories and flat color '
    'shapes. Every repeating protagonist maintains their established colors, hat, accessories and '
    'facial traits across shots. Other people must also have stick-figure anatomy, distinguishable '
    'by accessory and silhouette. Do not inject people into object-only or empty shots. '
    'CINEMATOGRAPHY: Specify physical foreground, midground and background subjects, exact left/right '
    'placement, scale relative to frame, camera distance and eye height, focal hierarchy, negative '
    'space and lighting direction. Use one clear shot rather than collage; vary POV, close-ups, wide '
    'establishing frames, cutaway diagrams and objects when the actual sentence calls for them. '
    'PROPS AND MATERIALS: Describe every important grounded object with shape, orientation, '
    'construction, connections, small legible markings only if supplied, worn edges, interaction '
    'with character, and spatial relationships. For subterranean layouts use precise flat orthographic '
    'cutaway geometry; for city streets, subway cars and modern disaster environments use suitably '
    'clear illustrated architecture rather than forcing every scene into a bunker. '
    'EXPRESSION AND NARRATIVE: If a stick person is present, specify eye direction, eyebrow/mouth '
    'expression, pose and the causal moment shown, maintaining recognizable design. '
    'COMPOSITION SAFETY: strictly 16:9, a single readable still, no extra panels, no floating text '
    'or captions unless factual numbers or labels are explicitly grounded in the source sentence. '
    'OUTPUT ONLY the specified VISUAL format, and check that EACH NANO BANANA PROMPT is 2,000-4,000 '
    'characters before replying; never shorten the last prompt or leave it unfinished. '
)

def visual_request(items,topic):
    if MODE=='survival':
        lines=[f'VISUAL {row["number"]} SOURCE: {row["sentence"]}' for row in items]
        return (f'Create NEXT EXACT {len(items)} standalone Google Nano Banana IMAGE prompts for '
          f'the fictional animated survival story {topic!r}. These are EXACT spoken sentences '
          'in original order; do not reword, skip or invent any sentence. Exactly one visual '
          'per supplied sentence, with precisely the requested numbers. The story protagonist '
          'must have one stable, distinctive character design throughout this project; '
          'introduce distinct secondary characters, and vary the main protagonist between videos. '
          'Whenever people are depicted, every person MUST be drawn as a real thin-limbed stick man with a small round pale head; keep reference bunker infographic cartoon style, NOT rounded fleshy cartoon people. '
          'DYNAMIC PACING: front-load visually distinctive opening moments, alternate calmer establishing beats with faster-feeling shot variation during threats and discoveries, without changing exact script mapping. '
          'Mix about 60-70 percent narrative scenes, 15-20 percent tight zoom/close-up shots '
          '(faces, doors, hands, objects, POV/peephole), and 15-20 percent clean number/text/chart '
          'infographics WHEN relevant to the exact sentence. Vary wide, overhead, close-up, '
          'environment, interior, exterior and object-only shots. For graphs show only numbers '
          'grounded in the source sentence; never fabricate quantities. No infographic or numbers '
          'if unsupported. Always maintain clear, professional 16:9 composition and consistent '
          'modern cartoon identity. ' + SURVIVAL_DETAILED_GUIDANCE + ' EVERY prompt must independently include this art style: '
          +DOC_STYLE+'\n\nOUTPUT PLAIN TEXT, NO TABLE, NO CODE FENCES. EXACT FORMAT:\n'
          'VISUAL #\nSCRIPT SENTENCE:\n"<exact SOURCE sentence>"\nVISUAL PURPOSE:\n'
          '<one sentence>\nNANO BANANA PROMPT:\n<detailed single-paragraph image prompt>\n\n'
          'HERE ARE THE ONLY SOURCE SENTENCES:\n'+'\n'.join(lines)+'\n\n'
          f'FINISH WITH BATCH_COMPLETE_{items[-1]["number"]}')
    lines=[]
    for row in items:lines.append(f'VISUAL {row["number"]} SOURCE: {row["sentence"]}')
    return (f'Create the NEXT EXACT {len(items)} standalone Google Nano Banana IMAGE prompts for the '
      f'prehistoric documentary {topic!r}. These are REAL spoken sentences, in their original order. '
      'Do not change, summarize, add or skip a sentence; exactly one visual for each supplied sentence. '
      'Generate precisely the requested numbers, and no other numbered VISUALS. Make every prompt ' 
      'self-contained and sufficiently different; alternate character, environment, diagram, '
      'infographic, reconstruction and scientific explanation. Apply this style in EVERY prompt: '
      +DOC_STYLE+'\n\n'
      'OUTPUT PLAIN TEXT, NO TABLE AND NO CODE FENCES. EXACT FORMAT FOR EACH:\n'
      'VISUAL #\nSCRIPT SENTENCE:\n"<exact SOURCE sentence>"\nVISUAL PURPOSE:\n<one sentence>\n'
      'NANO BANANA PROMPT:\n<detailed image prompt, single paragraph>\n\n'
      'HERE ARE THE ONLY SOURCE SENTENCES:\n'+'\n'.join(lines)+ '\n\n'
      f'FINISH WITH BATCH_COMPLETE_{items[-1]["number"]}')


def check_batch(text, rows):
    sys.path.insert(0,str(FLOW))
    from prompts import extract_from_messages
    got=extract_from_messages([text])
    ids=[x.number for x in got]
    expected=[r['number'] for r in rows]
    if ids!=expected:raise UIBlocked(f'ChatGPT visual numbers {ids}, expected {expected}; no Flow jobs submitted.')
    for row in rows:
        m=re.search(rf'(?ims)^\s*VISUAL\s+{row["number"]}\s*$(.*?)(?=^\s*VISUAL\s+\d+\s*$|\Z)',text)
        if not m or not re.search(r'SCRIPT\s+SENTENCE\s*:',m.group(1),re.I):
            raise UIBlocked(f'VISUAL {row["number"]} has no SCRIPT SENTENCE field.')
        block=m.group(1)
        match=re.search(r'(?ims)^\s*SCRIPT\s+SENTENCE\s*:\s*(.*?)(?=^\s*VISUAL\s+PURPOSE\s*:|^\s*NANO\s+BANANA\s+PROMPT\s*:|\Z)',block)
        if not match or norm(match.group(1))!=norm(row['sentence']):
            raise UIBlocked(f'VISUAL {row["number"]} sentence differs from the original narration. Do not submit wrong image.')
    return got


def find_existing_visual_reply(chat, rows):
    """Find an already-posted, exactly matching batch in recent replies."""
    for answer in recent_assistants(chat, limit=5):
        try:
            check_batch(answer, rows)
            log(f'VISUAL {rows[0]["number"]}-{rows[-1]["number"]}: matching existing ChatGPT reply found; no repost.')
            return answer
        except (UIBlocked, ValueError):
            continue
    return None


def check_audio(audio):
    cmd=['ffprobe','-v','error','-show_entries','format=duration','-of','default=nokey=1:noprint_wrappers=1',str(audio)]
    try:
        duration=float(subprocess.check_output(cmd,text=True,stderr=subprocess.STDOUT).strip())
    except Exception as e:raise UIBlocked(f'Audio file invalid or FFmpeg missing: {audio}: {e}')
    if duration<30:raise UIBlocked(f'Audio unexpectedly short: {audio} ({duration:.1f}s).')
    return duration


def verify_audio_length(p,part,audio):
    duration=check_audio(audio)
    words=len(p.path(f'script/part_{part}.txt').read_text(encoding='utf8').split())
    if not (words / 4.2 <= duration <= words / 0.9):
        raise UIBlocked(f'Part {part} audio is {duration:.1f}s for {words} script words; likely truncated or incorrect. Review audio before generating expensive images.')
    return duration


class Project:
    def __init__(self,folder):
        self.folder=Path(folder);self.meta=self.folder/'project.json'
        self.data=readjson(self.meta,{}) or {}
    def save(self):atomic(self.meta,self.data)
    def path(self,relative):return self.folder/relative
    def status(self,stage):return self.data.setdefault('status',{}).get(stage)
    def set(self,stage,value='done'):
        self.data.setdefault('status',{})[stage]=value;self.save()
    def warn(self,e):
        self.data['last_error']=str(e);self.save()


def make_project(title):
    name=dt.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+slug(title)
    path=PROJROOT/name
    if path.exists():raise UIBlocked('Timestamp project collision; refuse to reuse prior folder.')
    for section in ['script','audio','images','images/prompts','diagnostics','edit','FlowPromptAgentData']:
        (path/section).mkdir(parents=True,exist_ok=False)
    p=Project(path);p.data={'title':title,'mode':MODE,'created_at':dt.datetime.now().isoformat(),
        'status':{},'chat_url':'','next_batch':1,'tts_uncertain':None};p.save()
    history=readjson(CONTROL/'history.json',[]) or []
    history.append({'title':title,'folder':str(path),'created_at':p.data['created_at']})
    atomic(CONTROL/'history.json',history)
    atomic(CONTROL/'current.json',{'project':str(path)})
    return p


def load_active():
    current=readjson(CONTROL/'current.json',{}) or {}
    if current.get('project') and Path(current['project']).is_dir():
        p=Project(current['project'])
        if p.data.get('mode','ancient')!=MODE:
            raise UIBlocked(f'Active project mode {p.data.get("mode","ancient")} differs from selected {MODE}. Use the correct launcher; no project modified.')
        if p.status('video')!='done' or p.status('youtube')!='done':return p
    return None


def configure_flow(p):
    config=readjson(FLOW/'config.json',{}) or {}
    config.update({'output_dir':str(p.path('images')),'working_dir':str(p.path('FlowPromptAgentData')),
                  'cdp_url':'http://127.0.0.1:9333'})
    atomic(FLOW/'config.json',config)
    return config


def invoke_flow(p, python, batch_size=15, on_prompts_loaded=None, prompt_file=None):
    """Run Flow while allowing ONE next ChatGPT batch after its reader has loaded.

    No next-batch request is sent until the Flow subprocess explicitly reports
    that it has read this batch, avoiding a latest-assistant race.
    """
    configure_flow(p)
    env=os.environ.copy();env['PIPELINE_CHAT_URL']=p.data['chat_url'];env['PYTHONUNBUFFERED']='1'
    if prompt_file is not None:
        env['PIPELINE_PROMPTS_FILE']=str(prompt_file)
    cmd=[str(python),'-u',str(FLOW/'batch_agent.py'),'--max',str(batch_size)]
    log(f'Generating and downloading Flow images for the current {batch_size}-image batch...')
    # Batch silence watchdog: restart the subprocess after 30 seconds without
    # stdout. Never clear state, recreate the project, or turn on discard mode.
    # Recovery after restarting is delegated to the existing journal checks.
    stall_seconds = max(5, int(os.getenv('FLOW_NO_RESPONSE_RESTART_SECONDS', '360')))
    restart_limit = max(0, int(os.getenv('FLOW_NO_RESPONSE_RESTART_LIMIT', '3')))
    loaded = False
    prefetch_error = None
    for restart_number in range(restart_limit + 1):
        child=subprocess.Popen(cmd,cwd=str(FLOW),env=env,stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT,text=True,bufsize=1,errors='replace')
        lines=queue.Queue()
        def reader(proc, output):
            try:
                for item in proc.stdout:
                    output.put(item)
            finally:
                output.put(None)
        pump=threading.Thread(target=reader,args=(child,lines),daemon=True)
        pump.start()
        last_output=time.monotonic()
        silent=False
        while True:
            try: line=lines.get(timeout=0.5)
            except queue.Empty:
                if child.poll() is not None and not pump.is_alive():
                    break
                if time.monotonic()-last_output >= stall_seconds:
                    silent=True
                    log(f'WATCHDOG: no Flow agent response for {stall_seconds}s. '
                        f'Restarting current batch ({restart_number+1}/{restart_limit}); '
                        'saved image/journal progress stays unchanged.')
                    child.terminate()
                    try: child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        child.kill();child.wait(timeout=5)
                    break
                continue
            if line is None:
                break
            last_output=time.monotonic()
            print(line,end='',flush=True)
            if (not loaded and on_prompts_loaded is not None and
                re.search(r'VISUAL\s+\d+:\s+submitted;\s+proceeding to next prompt',line,re.I)):
                loaded=True
                try:
                    on_prompts_loaded()
                except Exception as exc:
                    prefetch_error=exc
                    log(f'NEXT-BATCH PREFETCH STOPPED: {exc}. Current Flow batch continues.')
        code=child.wait()
        pump.join(timeout=2)
        if silent:
            if restart_number < restart_limit:
                # Reopening must run through the existing journal checks.
                # In particular, uncertain paid submissions must not be reissued.
                continue
            raise UIBlocked(f'Flow agent was silent for {stall_seconds}s on '
                            f'{restart_limit+1} attempts. Stopped safely; existing '
                            'downloads, image cards and journal are preserved.')
        if code:
            raise UIBlocked(f'Flow image agent stopped (exit {code}). '
                            'Review its journal. No blind retry.')
        break
    if prefetch_error:
        log(f'NEXT BATCH PREFETCH DEFERRED: {prefetch_error}. '
            'Current Flow images are preserved; the next batch will recover normally.')


def completed_images(p,rows):
    journal=readjson(p.path('FlowPromptAgentData/state.json'),{}) or {}
    records=journal.get('items',{})
    for row in rows:
        r=records.get(str(row['number']),{})
        path=Path(r.get('file','')) if r.get('file') else None
        if r.get('status')!='completed' or path is None or not path.is_file() or path.stat().st_size<5000:
            return False
        if str(path.parent.resolve()).casefold()!=str(p.path('images').resolve()).casefold():
            raise UIBlocked(f'Image outside project folder: {path}; refusing to mark complete.')
    return True



# SURVIVAL_AUTO_RESET_INTERRUPTED_BATCH_V1
# Explicit opt-in behavior requested by user: new paid jobs may duplicate old Flow jobs.
def reset_interrupted_survival_batch(p, rows):
    """Archive only this interrupted batch; permit fresh Flow jobs after a crash.

    Never deletes historic records or already completed earlier batches. Run only
    when preflight detects non-verified Flow records in this specific batch.
    """
    start, end = rows[0]['number'], rows[-1]['number']
    wd = p.path('FlowPromptAgentData')
    journal = wd/'state.json'
    stage = wd/'fast_batch_staging'/f'{start:03d}-{end:03d}'
    destroot = p.path('recovery_backups')
    stamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    dest = destroot/f'auto_retry_{start:03d}_{end:03d}_{stamp}'
    dest.mkdir(parents=True,exist_ok=False)
    # Never change project.json, chat prompts, scripts, voice/audio or past images.
    data=readjson(journal,{}) or {}
    items=data.get('items',{})
    for row in rows:
        num=row['number']
        if str(num) in items:
            # Copy full prior journal *before* editing its contents.
            if journal.exists() and not (dest/'state.json').exists():
                shutil.copy2(journal,dest/'state.json')
            break
    old={str(row['number']):items[str(row['number'])] for row in rows if str(row['number']) in items}
    if old:
        atomic(dest/'batch_records.json',old)
    # Move only local files belonging to this batch to the recovery backup.
    image_dest=dest/'images'
    for row in rows:
        num=row['number']
        for img in p.path('images').glob(f'visual_{num:03d}.*'):
            if img.is_file():
                image_dest.mkdir(parents=True,exist_ok=True)
                shutil.move(str(img),str(image_dest/img.name))
    if stage.exists():
        shutil.move(str(stage),str(dest/'fast_batch_staging'))
    # If a matching review file is adjacent to this batch, archive it too.
    # All other Flow staging folders and downloaded images are preserved.
    if old:
        for key in old:
            items.pop(key,None)
        data['items']=items
        atomic(journal,data)
    log(f'AUTO RETRY: archived interrupted VISUAL {start}-{end} to {dest}. '
        'Previous paid Flow requests may be duplicated. Saved ChatGPT prompts unchanged.')


def process_project(p,context,args):
    if p.data.get('mode','ancient')!=MODE:
        raise UIBlocked('Project mode mismatch; refusing mixed-channel resume.')
    from browser_ops import resume_chat
    p.folder.mkdir(parents=True,exist_ok=True)
    if not p.data.get('chat_url'):
        raise UIBlocked('Project has no ChatGPT URL; cannot safely resume. Review project.json.')
    if p.status('video')=='done':
        configure_flow(p)
        from sfx_auto import ensure_project_sfx
        ensure_project_sfx(p)
        if MODE=='survival':
            from music_auto import ensure_survival_music
            ensure_survival_music(p)
        from thumbnail_auto import ensure_thumbnail
        ensure_thumbnail(p, context, BASE)
        from youtube_publish import publish
        publish(p,BASE,args.youtube_visibility)
        # PERMANENT_SHORTS_AFTER_VIDEO_GATE_V1: only completed videos enter Shorts.
        if MODE == 'survival' and p.status('video') == 'done' and p.status('youtube') == 'done':
            from shorts_pipeline import ensure_shorts
            ensure_shorts(p, BASE, args.youtube_visibility)
        return
    chat=resume_chat(context,p.data['chat_url'])
    log(f'Project: {p.data["title"]}\nFolder: {p.folder}')
    for part in (1,2):
        file=p.path(f'script/part_{part}.txt')
        if not file.exists():
            previous=p.path('script/part_1.txt').read_text('utf-8') if part==2 else ''
            if p.data.get('script_pending')==part:
                answer=latest_assistant(chat)
                try:parse_script(answer,part)
                except Exception:
                    raise UIBlocked(f'Part {part} script request may already have been sent. Check ChatGPT; no blind duplicate submission.')
            else:
                p.data['script_pending']=part;p.save()
                answer=chat_send(chat,script_request(p.data['title'],part,previous),f'SCRIPT_PART_{part}_END')
                        # Automatically retry scripts outside the allowed word range.
            max_retries = 5

            for attempt in range(max_retries + 1):
                try:
                    text = parse_script(answer, part)
                    break

                except UIBlocked as error:
                    if "words; expected" not in str(error):
                        raise

                    if attempt == max_retries:
                        raise UIBlocked(
                            f"Part {part} failed word count after {max_retries} retries."
                        )

                    log(
                        f"Part {part}: outside word limit. "
                        f"Requesting rewrite {attempt + 1}/{max_retries}..."
                    )

                    answer = chat_send(
                        chat,
                        f"""Rewrite your previous Part {part} narration.

Your previous response failed the automatic word-count check.

REQUIREMENTS:
- Between 700 and 1200 words.
- Target approximately 950 words.
- Preserve the topic and {'animated survival-story' if MODE=='survival' else 'documentary'} storytelling.
- No headings, explanations, or stage directions.
- Return the COMPLETE narration.
- Do not return only the changed sentences.

Use exactly these boundary markers:

SCRIPT_PART_{part}_START
<complete spoken narration>
SCRIPT_PART_{part}_END
""",
                        f"SCRIPT_PART_{part}_END",
                    )
            save_text(file,text)
            p.data['script_pending']=None
            p.set('script'+str(part))
            log(f'Saved script Part {part}: {len(text.split())} words')
    p.set('scripts')
    if args.stop_after=='scripts':return
    audio_files=[]
    for part in (1,2):
        audio_dir=p.path('audio')
        candidates=[x for x in audio_dir.glob(f'part_{part}.*') if x.suffix.lower() in VOICE_EXTS and x.stat().st_size>10000]
        if candidates:
            actual=candidates[0];verify_audio_length(p,part,actual);audio_files.append(actual)
            p.set(f'audio{part}');continue
        if p.data.get('tts_uncertain')==part:
            from recover_existing_audio import recover_existing_audio
            recovered = recover_existing_audio(context,p,part)
            verify_audio_length(p,part,recovered)
            p.data['tts_uncertain']=None;p.set(f'audio{part}')
            audio_files.append(recovered)
            continue
        studio=context.new_page()
        try:
            studio.goto(AI_STUDIO_URL,wait_until='domcontentloaded',timeout=60000)
            def mark():p.data['tts_uncertain']=part;p.save()
            generated=studio_make_part(studio,p.path(f'script/part_{part}.txt').read_text(encoding='utf-8'),p.path(f'audio/part_{part}.wav'),p.path('diagnostics'),part,mark)
            verify_audio_length(p,part,generated)
            p.data['tts_uncertain']=None;p.set(f'audio{part}')
            audio_files.append(generated)
        finally:studio.close()
    p.set('audios')
    if args.stop_after=='audio':return
    scenes_file=p.path('edit/scenes.json')
    if not scenes_file.exists():
        alltext='\n\n'.join(p.path(f'script/part_{x}.txt').read_text('utf8') for x in (1,2))
        sentences=split_sentences(alltext)
        if not 15<=len(sentences)<=350:
            raise UIBlocked(f'Unreasonable scene count {len(sentences)}; review scripts/splitting first.')
        scenes=[{'number':i,'sentence':s} for i,s in enumerate(sentences,1)]
        atomic(scenes_file,scenes)
        atomic(p.path('edit/scene_map.json'),{str(x['number']):x['sentence'] for x in scenes})
        log(f'Planned {len(scenes)} separate visuals, with every spoken sentence covered.')
    scenes=readjson(scenes_file,[])
    # Preserve existing 10-image projects; use 15 for new projects.
    batch_size=p.data.get('image_batch_size')
    if batch_size not in (2,10,12,15):
        prompts_folder=p.path('images/prompts')
        journal=readjson(p.path('FlowPromptAgentData/state.json'),{}) or {}
        has_legacy=any(prompts_folder.glob('*.txt')) or bool(journal.get('items'))
        batch_size=10 if has_legacy else (12 if MODE=='survival' else 15)
        p.data['image_batch_size']=batch_size;p.save()
        log(f'Image batch size locked for this project: {batch_size}.')
    switch_at = int(p.data.get('image_batch_switch_at', 1))
    if MODE == 'survival' and switch_at > 1:
        prefix = scenes[:switch_at - 1]
        suffix = scenes[switch_at - 1:]
        batches = (
            [prefix[i:i+2] for i in range(0,len(prefix),2)] +
            [suffix[i:i+batch_size] for i in range(0,len(suffix),batch_size)]
        )
    else:
        batches=[scenes[i:i+batch_size] for i in range(0,len(scenes),batch_size)]
    p.set('storyboard')
    if args.stop_after=='storyboard':return
    configure_flow(p)
    count=0
    def prepare_next_rows(next_rows):
        """Prefetch at most the very next batch; persist before returning."""
        if not next_rows or completed_images(p,next_rows):return
        first,last=next_rows[0]['number'],next_rows[-1]['number']
        target=p.path(f'images/prompts/{first:03d}-{last:03d}.txt')
        if target.exists():
            check_batch(target.read_text(encoding='utf-8-sig'),next_rows)
            return
        if p.data.get('visuals_pending') not in (None,first):
            # A stale pending marker from an ALREADY VERIFIED earlier batch must
            # not block prefetch of the next batch. Never clear an unverified one.
            old_first = p.data['visuals_pending']
            old_rows = next((group for group in batches
                             if group[0]['number'] == old_first), None)
            if old_rows is not None and completed_images(p, old_rows):
                log(f'NEXT-BATCH PREFETCH: clearing stale pending VISUAL {old_first}; '
                    'all images in that earlier batch are verified.')
                p.data['visuals_pending'] = None
                p.save()
            else:
                raise UIBlocked(f'Different pending visual batch {old_first}; no prefetch.')
        request=visual_request(next_rows,p.data['title'])
        if p.data.get('visuals_pending')==first:
            existing=find_existing_visual_reply(chat,next_rows)
            if existing is not None:
                answer=existing
            else:
                status=chat_request_visible(chat,request)
                if status is True:
                    # A previous request is posted; wait for completion without resending.
                    deadline=time.monotonic()+300
                    while time.monotonic()<deadline:
                        time.sleep(3)
                        answer=latest_assistant(chat)
                        try: check_batch(answer,next_rows);break
                        except Exception: pass
                    else:
                        raise UIBlocked(f'Next batch {first}-{last} already posted, response incomplete. No resend.')
                elif status is False:
                    answer=chat_send(chat,request,f'BATCH_COMPLETE_{last}',timeout=300)
                else:
                    raise UIBlocked('Cannot verify previously pending ChatGPT batch; no resend.')
        else:
            p.data['visuals_pending']=first;p.save()
            answer=chat_send(chat,request,f'BATCH_COMPLETE_{last}',timeout=300)
        check_batch(answer,next_rows)
        save_text(target,answer)
        p.data['visuals_pending']=None;p.save()
        log(f'NEXT BATCH READY: VISUAL {first}-{last}, saved while Flow worked.')

    for batch_index,rows in enumerate(batches):
        n=rows[0]['number'];end=rows[-1]['number']
        textfile=p.path(f'images/prompts/{n:03d}-{end:03d}.txt')
        if completed_images(p,rows):
            if MODE == 'survival':
                from batch_upscale_2k import ensure_batch_2k
                ensure_batch_2k(p, rows)  # PERMANENT_POST_BATCH_LOCAL_2K_V1
            log(f'VISUAL {n}-{end} already verified; skipping.');continue

        # LOCAL_PNG_RECOVERY_V2
        # Only skip when every numbered image exists locally.
        recovered = {}
        for row in rows:
            num = row['number']
            candidates = [
                p.path('images') / f'visual_{num}.png',
                p.path('images') / f'visual_{num:03d}.png',
                p.path('images') / f'visual_{num}.jpg',
                p.path('images') / f'visual_{num:03d}.jpg',
            ]
            found = next(
                (f for f in candidates
                 if f.is_file() and f.stat().st_size > 5000),
                None
            )
            if found is not None:
                recovered[num] = found

        if len(recovered) == len(rows):
            if MODE == 'survival':
                from batch_upscale_2k import ensure_batch_2k
                ensure_batch_2k(p, rows)
            log(f'VISUAL {n}-{end}: all recovered local images present; skipping Flow.')
            continue
        # PREFETCH_JOURNAL_PREFLIGHT_V1: do not touch ChatGPT or Flow if this
        # batch's previous paid image requests may still exist in the gallery.
        # Existing completed local files and journal entries are preserved.
        records=(readjson(p.path('FlowPromptAgentData/state.json'),{}) or {}).get('items',{})
        uncertain={}
        for row in rows:
            num=row['number']
            item=records.get(str(num)) or {}
            status=item.get('status')
            if status in (None,'pending'):
                continue
            if status=='completed':
                file=Path(item['file']) if item.get('file') else None
                if (file is not None and file.is_file() and file.stat().st_size>5000
                    and str(file.parent.resolve()).casefold()==str(p.path('images').resolve()).casefold()):
                    continue
                status='completed-but-local-file-unverified'
            uncertain[num]=status
        if uncertain:
            description=', '.join(f'{num}:{status}' for num,status in sorted(uncertain.items()))
            log(f'VISUAL {n}-{end}: interrupted Flow journal ({description}). '
                'Handing recovery to the Flow agent; no journal entries cleared or paid jobs blindly repeated.')
        # SURVIVAL_AUTO_RESET_INTERRUPTED_BATCH_V1
        if MODE == 'survival' and uncertain and not recovered:
            reset_interrupted_survival_batch(p, rows)
        if not textfile.exists():
            flag=p.data.get('visuals_pending')
            if flag==n:
                # CHAT_VISUAL_PENDING_RECOVERY_V2: an intent flag is NOT proof of delivery.
                # If the exact request is posted, wait/recover its result; if clearly
                # absent, retry once via the normal verified-composer workflow.
                request = visual_request(rows,p.data['title'])
                answer = find_existing_visual_reply(chat,rows)
                if answer is not None:
                    log(f'VISUAL {n}-{end}: recovered complete existing ChatGPT reply.')
                else:
                    # Ensure the chat DOM has loaded before inferring absence.
                    chat.locator('body').wait_for(state='visible',timeout=15000)
                    seen = chat_request_visible(chat, request)
                    if seen is True:
                        # Existing submitted request: do not duplicate it.
                        log(f'VISUAL {n}-{end}: request is posted; waiting for original reply.')
                        deadline = time.monotonic() + 180
                        while time.monotonic() < deadline:
                            time.sleep(3)
                            answer = latest_assistant(chat)
                            if f'BATCH_COMPLETE_{end}' in answer:
                                check_batch(answer, rows)
                                break
                        else:
                            raise UIBlocked(f'VISUAL {n}-{end} request was posted but reply not complete. Do not duplicate; inspect ChatGPT.')
                    elif seen is False:
                        # A long posted prompt may be truncated or virtualized by
                        # ChatGPT; DOM absence does not prove it was never sent.
                        # Do not create duplicates after a crash/disconnect.
                        raise UIBlocked(f'VISUAL {n}-{end}: pending request could not be confirmed in the loaded chat. '
                                        'NO duplicate sent. Inspect the ChatGPT tab and recover the existing reply.')
                    else:
                        raise UIBlocked(f'VISUAL {n}-{end}: unable to inspect posted ChatGPT messages. No automatic resend.')
            else:
                answer = find_existing_visual_reply(chat, rows)
                if answer is None:
                    p.data['visuals_pending']=n;p.save()
                    answer=chat_send(chat,visual_request(rows,p.data['title']),f'BATCH_COMPLETE_{end}',timeout=300)
            check_batch(answer,rows)
            save_text(textfile,answer)
            p.data['visuals_pending']=None;p.save()
            log(f'Validated ChatGPT prompts for VISUAL {n}-{end}.')
        else:
            check_batch(textfile.read_text('utf-8-sig'), rows)
            # Flow reads PIPELINE_PROMPTS_FILE directly in invoke_flow().
            # A newer ChatGPT message does not invalidate this verified batch.
            # Never repost or replace paid-image prompts merely to make them latest.
            log(f'VISUAL {n}-{end}: using verified saved prompts; no ChatGPT repost.')

        # AUTOMATIC IMAGE BATCH RESTART RULE
        max_batch_restarts = 2

        for restart in range(max_batch_restarts + 1):
            try:
                next_rows=batches[batch_index+1] if batch_index+1<len(batches) else None
                invoke_flow(p, args.python, batch_size=len(rows), prompt_file=textfile,
                            on_prompts_loaded=(lambda nxt=next_rows: prepare_next_rows(nxt))
                            if next_rows else None)

                if not completed_images(p, rows):
                    raise UIBlocked(
                        f"VISUAL {n}-{end}: incomplete downloads"
                    )

                break

            except UIBlocked as error:
                if ("Flow image agent stopped" in str(error) or
                    "incomplete downloads" in str(error)):
                    raise

                if restart >= max_batch_restarts:
                    raise

                log("Restarting failed image batch...")
                # Clear ONLY this batch's Flow journal entries.
                state_path = p.path("FlowPromptAgentData/state.json")
                state = readjson(state_path, {}) or {}
                items = state.get("items", {})

                for visual in rows:
                    number = visual["number"]
                    items.pop(str(number), None)

                    # Delete this batch's local downloaded images.
                    for image in p.path("images").glob(
                        f"visual_{number:03d}.*"
                    ):
                        if image.is_file():
                            image.unlink()

                state["items"] = items
                atomic(state_path, state)

                # Request entirely new Nano Banana prompts
                # for the SAME five script sentences.
                request = visual_request(rows, p.data["title"])

                request += (
                    "\nIMPORTANT: Previous image generation failed. "
                    "Create NEW, visually different prompts for "
                    "these exact sentences. Keep the exact VISUAL "
                    "numbers and output format."
                )

                # Mark the outgoing request before sending it.
                p.data["visuals_pending"] = n
                p.save()

                answer = chat_send(
                    chat,
                    request,
                    f"BATCH_COMPLETE_{end}",
                    timeout=300
                )
                check_batch(answer, rows)
                save_text(textfile, answer)

                p.data["visuals_pending"] = None
                p.save()

                time.sleep(5)

        if not completed_images(p, rows):
            raise UIBlocked(
                f'VISUAL {n}-{end} not all verified; inspect journal.'
            )
        if MODE == 'survival':
            from batch_upscale_2k import ensure_batch_2k
            ensure_batch_2k(p, rows)

        count += 1
        p.data['next_batch'] = end + 1
        p.save()

        if args.max_batches and count >= args.max_batches:
            log(
                f'Stopped after {count} generated batch(es) as requested. '
                'Rerun the same command to resume.'
            )
            return

    p.set('images')
    if args.stop_after == 'images':
        return

    p.set('video', 'running')
    audio = []
    for i in (1, 2):
        matches=[x for x in p.path('audio').glob(f'part_{i}.*') if x.suffix.lower() in VOICE_EXTS]
        if len(matches)!=1:raise UIBlocked(f'Expected one audio file for Part {i}; found {len(matches)}.')
        check_audio(matches[0]);audio.append(matches[0])
    cmd=[str(args.python),'-u',str(SYNC),'--audio1',str(audio[0]),'--audio2',str(audio[1]),
         '--images',str(p.path('images')),'--scene-map',str(p.path('edit/scene_map.json')),
         '--work',str(p.path('edit')),'--output',str(p.path('final_video.mp4')),
         '--end',str(len(scenes)),'--model',args.whisper_model]
    log('Aligning original sentence timings with Whisper and rendering video...')
    code=subprocess.call(cmd,cwd=str(BASE))
    if code:raise UIBlocked(f'Synchronization or rendering failed (exit {code}); see {p.path("edit/timings_review.csv")}.')
    if not p.path('final_video.mp4').is_file():raise UIBlocked('FFmpeg returned but final_video.mp4 was not found.')
    p.set('video');log('FINISHED: '+str(p.path('final_video.mp4')))
    configure_flow(p)
    from sfx_auto import ensure_project_sfx
    ensure_project_sfx(p)
    if MODE=='survival':
        from music_auto import ensure_survival_music
        ensure_survival_music(p)
    from thumbnail_auto import ensure_thumbnail
    ensure_thumbnail(p, context, BASE)
    from youtube_publish import publish
    publish(p,BASE,args.youtube_visibility)
    # POST_UPLOAD_SHORTS_STAGE_V1: run after main upload; state prevents duplicates.
    if MODE == 'survival' and p.status('video') == 'done' and p.status('youtube') == 'done':
        from shorts_pipeline import ensure_shorts
        ensure_shorts(p, BASE, args.youtube_visibility)


def run(args):
    if not args.python.is_file():raise RuntimeError(f'Python environment missing: {args.python}. Run Install.ps1 first.')
    if not args.guide.is_file():raise RuntimeError(f'Required guide PDF not found: {args.guide}')
    if args.fresh_start:
        CONTROL.mkdir(parents=True,exist_ok=True)
        active=readjson(CONTROL/'current.json',{}) or {}
        if active.get('project'):
            old=Path(active['project'])
            log('Preserving old project unchanged: '+str(old))
            archive=CONTROL/'previous_active.json'
            atomic(archive,{'project':str(old),'archived_at':dt.datetime.now().isoformat()})
        atomic(CONTROL/'current.json',{})
        log('Fresh-start flag cleared; the next run will resume the new project, not start over.')
    if args.dry_run:
        used,names=used_titles()
        print('DRY RUN (no ChatGPT, Google or Flow requests).',flush=True)
        print('Used topics: '+', '.join(names),flush=True)
        print('Future project root: '+str(PROJROOT),flush=True)
        print('Chrome CDP: '+args.cdp,flush=True)
        print('Voice: AI Studio > Create dialogue > Storyteller > Koda',flush=True)
        print('Narration style: '+STYLE,flush=True)
        print('Mode: '+MODE,flush=True)
        print('Image style: '+DOC_STYLE,flush=True)
        print('One image per sentence; every project has its own images and journal.',flush=True)
        return
    if not shutil.which('ffprobe') or not shutil.which('ffmpeg'):
        raise RuntimeError('ffmpeg/ffprobe missing on PATH. Install FFmpeg before running.')
    from playwright.sync_api import sync_playwright
    CONTROL.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as pw:
        browser=pw.chromium.connect_over_cdp(args.cdp,timeout=25000)
        try:
            if not browser.contexts:raise UIBlocked('No Chrome context. Launch Chrome with remote-debugging-port=9333.')
            context=browser.contexts[0]
            processed=0
            while args.videos==0 or processed<args.videos:
                p=load_active()
                if not p:
                    used,names=used_titles()
                    chat=new_chat(context)
                    attach_guide(chat,args.guide,CONTROL/'diagnostics')
                    topic=select_unused_topic(chat,names,used)
                    p=make_project(topic)
                    p.data['chat_url']=chat_url(chat);p.set('topic')
                    log('Selected NEW title: '+topic)
                if args.retry_voice_part and p.data.get('tts_uncertain')==args.retry_voice_part:
                    p.data['tts_uncertain']=None;p.save()
                try:process_project(p,context,args)
                except Exception as e:
                    snapshot(next((x for x in context.pages if x.url.split('?')[0]==p.data.get('chat_url','')),
                         context.pages[0]),p.path('diagnostics'),'last_failure')
                    p.warn(e);raise
                processed+=1
                if p.status('youtube')!='done':return # upload must finish before next project
                if args.videos==0 or processed<args.videos:
                    log('Completed one project; moving to another unused topic in 20 seconds...')
                    time.sleep(20)
        finally:
            # Playwright context exit disconnects CDP; never close the user's
            # separately launched Chrome automation profile here.
            pass


def main():
    ap=argparse.ArgumentParser(description='Switchable, resumable Ancient / Survival YouTube bot')
    ap.add_argument('--dry-run',action='store_true')
    ap.add_argument('--youtube-visibility',choices=['private','unlisted','public'],default='private')
    ap.add_argument('--fresh-start',action='store_true',help='Archive active pointer and start a NEW topic with the attached PDF, preserving old project files.')
    ap.add_argument('--guide',type=Path,default=Path.home()/'Downloads'/'Prompts.pdf',help='Actual PDF to upload into new ChatGPT chat')
    ap.add_argument('--videos',type=int,default=1,help='Number of fully complete new videos. 0 means continuous loop.')
    ap.add_argument('--max-batches',type=int,default=0,help='For testing: stop after this many generated image batches.')
    ap.add_argument('--stop-after',choices=['scripts','audio','storyboard','images'],default=None)
    ap.add_argument('--retry-voice-part',type=int,choices=[1,2],default=None,
                    help='Explicitly authorize retry after confirming prior AI Studio attempt did not produce audio.')
    ap.add_argument('--python',type=Path,default=Path.home()/'Downloads'/'FlowPromptAgent'/'.venv'/'Scripts'/'python.exe')
    ap.add_argument('--whisper-model',choices=['tiny','base','small','medium'],default='small')
    ap.add_argument('--cdp',default='http://127.0.0.1:9333')
    args=ap.parse_args()
    try:run(args)
    except KeyboardInterrupt:log('Interrupted safely. Progress is saved; rerun to resume.');sys.exit(130)
    except Exception as e:print('STOPPED: '+str(e),file=sys.stderr,flush=True);sys.exit(1)

if __name__=='__main__':main()


