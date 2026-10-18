import os
"""Browser actions for the user's existing Chrome remote-debugging session.
Conservative: never guess at a voice or repeat an uncertain paid generation.
"""
import re, time, hashlib, sys, os
os.environ.setdefault('PYTHONUTF8','1')
os.environ.setdefault('PYTHONIOENCODING','utf-8:replace')
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, 'reconfigure'):
        try: _stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception: pass
from pathlib import Path

AI_STUDIO_URL = 'https://aistudio.google.com/generate-speech?model=gemini-3.8-flash-tts'
STYLE = ('Deep, warm, cinematic documentary narration. Calm, mysterious and engaging storytelling '
         'with 1.2x pace, subtle suspense, expressive vocal variation and short dramatic pauses '
         'before important discoveries. Clear pronunciation, authoritative but conversational tone. '
         'Avoid shouting, robotic delivery or exaggerated acting.')
# Channel-specific narration override; Ancient mode remains unchanged.
if os.getenv('YOUTUBE_MODE', 'ancient').lower() == 'survival':
    STYLE = os.getenv(
        'YOUTUBE_VOICE_STYLE',
        'Natural, expressive American male voice with a medium-low pitch, warm depth, '
        'and a confident, conversational tone. Medium-fast pacing, crisp pronunciation, '
        'smooth rhythm, and controlled urgency. Build suspense through subtle pitch changes, '
        'emotional emphasis, and short dramatic pauses. Sound energetic, curious, serious, '
        'and immersive, like telling an intense survival story to a friend. '
        'Keep the delivery realistic, engaging, and human, never robotic, monotone, '
        'overly theatrical, or exaggerated.'
    )


# BEGIN_SURVIVAL_KODA_VOICE_STYLE
if os.getenv("YOUTUBE_MODE", "ancient").lower() == "survival":
    STYLE = 'talking from heart to gut,Deep, medium-low male voice. Speak in one steady, consistent tone, straight to the point, with a natural, confident,storytelling style. , with elongated vowels and clearly drawn-out words. Smooth, continuous delivery with minimal pauses and no sudden changes in pitch. Calm, serious, direct, and effortless, like a man telling a mysterious true story Natural pronunciation, normal vowel length, and crisp consonants. Do not stretch, drag, or overpronounce words. Say words like "matched" naturally and quickly, not "maaatched." Maintain a steady, masculine, conversational tone with smooth pacing and no exaggerated syllables. 20 yo man talking'
# END_SURVIVAL_KODA_VOICE_STYLE


def log(s): print(s, flush=True)

class UIBlocked(RuntimeError): pass

def snapshot(page, folder, name):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    try:page.screenshot(path=str(folder/(name+'.png')),full_page=False,timeout=15000)
    except Exception:pass
    try:(folder/(name+'.txt')).write_text(page.locator('body').inner_text(timeout=7000)[:17000],encoding='utf-8')
    except Exception:pass

def selected(locator):
    try:return locator.count()>0 and locator.first.is_visible(timeout=2000)
    except Exception:return False

def first_visible(page, selector_list):
    for s in selector_list:
        q=page.locator(s)
        for i in range(min(q.count(),8)):
            if selected(q.nth(i)):return q.nth(i)
    return None

# CHAT_DOM_HEADING_READER_V4
def _assistant_replies_from_dom(page, limit=15):
    """Read ChatGPT replies from both legacy role nodes and accessible h4 headings.

    The page may have no data-message-author-role attributes. Never parse a
    user request or the entire body as a reply. Each candidate MUST belong to
    one ChatGPT said: heading and must not contain another message heading.
    """
    return page.evaluate(r"""(limit) => {
      const trim = x => (x || '').replace(/\u00a0/g, ' ').trim();
      const replies=[];
      const legacy=[...document.querySelectorAll('[data-message-author-role="assistant"]')];
      for(const el of legacy) {const t=trim(el.innerText);if(t)replies.push(t);}
      if(replies.length) return replies.slice(-limit).reverse();
      const heads=[...document.querySelectorAll('h4')];
      for(const h of heads) {
        if(trim(h.innerText) !== 'ChatGPT said:') continue;
        let best='';
        for(let el=h.parentElement,depth=0;el&&depth<11;el=el.parentElement,depth++) {
          const t=trim(el.innerText);
          if(!t.startsWith('ChatGPT said:')) continue;
          // Reject containers containing other message headers: never mix turns.
          const headers=[...el.querySelectorAll('h4')].filter(x=>
            /^(ChatGPT said:|You said:)$/.test(trim(x.innerText)));
          if(headers.length!==1 || headers[0]!==h) continue;
          const content=t.replace(/^ChatGPT said:\s*/, '').trim();
          if(content && content !== 'ChatGPT can make mistakes. Check important info.') {
            best=content;break; // smallest containing single message only
          }
        }
        if(best) replies.push(best);
      }
      return replies.slice(-limit).reverse();
    }""", max(1, int(limit))) or []


def latest_assistant(page):
    items=_assistant_replies_from_dom(page,1)
    return items[0] if items else ''


def recent_assistants(page, limit=5):
    return _assistant_replies_from_dom(page,limit)




# CHAT_COMPOSER_RETRY_PATCH_V1

def _verified_composer(page):
    """Find an editable ChatGPT composer, not merely a visible textbox."""
    selectors = ['[data-testid="composer-text-input"]', '#prompt-textarea',
                 '[contenteditable="true"][role="textbox"][aria-label*="ChatGPT" i]',
                 '[contenteditable="true"][role="textbox"]']
    for css in selectors:
        candidates = page.locator(css)
        for i in range(min(candidates.count(), 8)):
            item = candidates.nth(i)
            try:
                if item.is_visible(timeout=1500) and item.is_enabled(timeout=1500) and item.is_editable(timeout=1500):
                    return item
            except Exception:
                pass
    return None



# CHAT_SEND_RECOVERY_V2
def chat_request_visible(page, prompt):
    """Return True only when the full outgoing request appears as a posted USER message.

    Composer drafts do not count. Never infer a successful send from clicking Send.
    """
    try:
        posted = page.locator('[data-message-author-role="user"]')
        # Long prompts may be represented as multiple paragraphs within one message.
        expected = re.sub(r'\s+', ' ', prompt).strip()
        for i in range(max(0, posted.count()-20), posted.count()):
            actual = re.sub(r'\s+', ' ', posted.nth(i).inner_text(timeout=3000)).strip()
            if actual == expected or (len(expected)>250 and expected[:100] in actual and expected[-100:] in actual):
                return True
    except Exception:
        return None   # Can't inspect DOM reliably; do not assume it was NOT sent.
    return False

def chat_send(page, prompt, marker=None, timeout=240):
    old = latest_assistant(page)
    # The ChatGPT composer can already contain the correct draft after a timeout.
    # Check BEFORE calling fill(), which can hang on a populated ProseMirror editor.
    def composer_normalize(value):
        return re.sub(r'\s+', ' ', (value or '').replace('\u00a0', ' ')).strip()

    expected = composer_normalize(prompt)
    deadline = time.monotonic() + 75
    editor = None
    last_error = None
    while time.monotonic() < deadline:
        try:
            if page.is_closed():
                raise UIBlocked('ChatGPT tab is closed; reopen the project chat and rerun.')
            if 'chatgpt.com' not in page.url:
                raise UIBlocked(f'Expected ChatGPT tab, got {page.url}; stop without sending.')
            # Inspect the existing draft without clicking or trying to overwrite it.
            for css in ('[data-testid="composer-text-input"]', '#prompt-textarea',
                        '[role="textbox"][aria-label*="ChatGPT" i]',
                        '[contenteditable="true"][role="textbox"]'):
                loc = page.locator(css)
                for i in range(min(loc.count(), 6)):
                    candidate = loc.nth(i)
                    if not candidate.is_visible(timeout=1200):
                        continue
                    draft = composer_normalize(candidate.inner_text(timeout=3000))
                    if draft:
                        if draft != expected:
                            raise UIBlocked('ChatGPT contains a DIFFERENT unsent draft; no message sent or overwritten. Review the ChatGPT input.')
                        editor = candidate
                        log('ChatGPT: full expected prompt is already pasted; sending without refilling.')
                        break
                if editor is not None:
                    break
            if editor is not None:
                break
            editor = _verified_composer(page)
            if editor is not None:
                editor.scroll_into_view_if_needed(timeout=3500)
                editor.click(timeout=3500)
                editor.fill(prompt, timeout=12000)
                typed = editor.inner_text(timeout=3000)
                if composer_normalize(typed) != expected:
                    raise UIBlocked('ChatGPT composer differs after paste; nothing sent.')
                break
        except UIBlocked:
            raise
        except Exception as e:
            last_error = e
        editor = None
        page.wait_for_timeout(2200)
    if editor is None:
        raise UIBlocked(f'ChatGPT composer unavailable (75s). Last error: {str(last_error)[:180]}. No prompt sent.')
    # Send button is preferred over Enter (which can insert a newline in some editors).
    button = None
    for css in ('button[data-testid="send-button"]', 'button[aria-label="Send prompt"]',
                'button[aria-label="Send message"]'):
        item = page.locator(css)
        if selected(item) and item.first.is_enabled():
            button = item.first
            break
    if button is not None:
        button.click(timeout=12000)
    else:
        editor.press('Enter', timeout=12000)
    log('ChatGPT: Send activated; checking the posted request and awaiting full response.')
    # Sending was ATTEMPTED; wait for the user's posted message as confirmation.
    # If not confirmed, stop as uncertain. The pipeline will inspect the posted
    # messages on resume and retry ONLY if it can prove this one is absent.
    # ChatGPT can take time to post long requests or stream a response. Do not
    # mistake delayed rendering of the outgoing bubble for a failed submission.
    confirmed = False
    deadline_sent = time.monotonic() + 300
    next_status = time.monotonic() + 20
    while time.monotonic() < deadline_sent:
        if page.is_closed():
            raise UIBlocked('ChatGPT tab closed after Send; submission uncertain. Resume to inspect chat.')
        seen = chat_request_visible(page, prompt)
        if seen is True:
            confirmed = True
            log('ChatGPT posted user message verified; waiting for generated prompts...')
            break
        # A new complete assistant answer with this batch marker is independent
        # evidence that the request succeeded even if the user bubble is hidden.
        reply = latest_assistant(page)
        if reply and reply != old and marker and marker in reply:
            confirmed = True
            log('ChatGPT completed batch response; outgoing bubble did not need verification.')
            break
        if time.monotonic() >= next_status:
            log('ChatGPT: still waiting for submitted request or completed answer; no duplicate sent.')
            next_status = time.monotonic() + 30
        time.sleep(2)
    if not confirmed:
        raise UIBlocked('No posted request or complete response verified after 5 minutes. Submission uncertain; inspect ChatGPT before retrying.')
    log('ChatGPT submission verified; waiting for complete reply...')
    # SurvivalChatWatcherFix: wait for a stable, complete reply, not a spinner.
    previous = None
    stable_since = None
    start = time.monotonic()
    last_status = start
    while time.monotonic()-start < timeout:
        time.sleep(2)
        try:
            if page.is_closed():
                raise UIBlocked('ChatGPT tab closed after submission; inspect the posted reply and resume without FreshStart.')
            content = latest_assistant(page)
            if not content or content == old or (marker and marker not in content):
                if time.monotonic()-last_status > 30:
                    log('ChatGPT: waiting for expected response marker; no new request sent.')
                    last_status = time.monotonic()
                continue
            digest = hashlib.sha256(content.encode('utf-8')).hexdigest()
            if digest != previous:
                previous = digest
                stable_since = time.monotonic()
            # A finished response must be stable and the ChatGPT Stop control
            # must be gone; use multiple known selectors across UI versions.
            busy = page.evaluate(r'''() => {
                const buttons = [...document.querySelectorAll('button')];
                return buttons.some(b => {
                    if (!b.getClientRects().length) return false;
                    const s = ((b.getAttribute('data-testid')||'')+' '+
                        (b.getAttribute('aria-label')||'')+' '+
                        (b.getAttribute('title')||'')).toLowerCase();
                    return /stop-button|stop generating|stop streaming|stop response/.test(s);
                });
            }''')
            if not busy and stable_since is not None and time.monotonic()-stable_since >= 12:
                log('ChatGPT: completed response detected, continuing with batch validation.')
                return content
            if time.monotonic()-last_status > 30:
                log('ChatGPT: response marker found; waiting for stable completion (no duplicate).')
                last_status = time.monotonic()
        except UIBlocked:
            raise
        except Exception as exc:
            # CDP disconnects/pages closing are not evidence of an unsent
            # request. Never retry a submitted request blindly.
            raise UIBlocked(f'ChatGPT reply watcher lost its browser page: {exc}. '
                            'Keep Chrome port 9333 open; resume the saved project, not FreshStart.') from exc
    raise UIBlocked('ChatGPT reply watcher timed out. Request remains pending; do not resend automatically.')

def new_chat(context):
    page=context.new_page()
    page.goto('https://chatgpt.com/',wait_until='domcontentloaded',timeout=60000)
    return page

def attach_guide(page, pdf_path, diagnostics):
    """Attach the user's real PDF to this new ChatGPT conversation before any prompt is sent."""
    pdf_path=Path(pdf_path).expanduser()
    if not pdf_path.is_file():
        raise UIBlocked(f'Required PDF missing: {pdf_path}. Topic request was NOT sent.')
    if pdf_path.suffix.lower()!='.pdf':
        raise UIBlocked(f'Guide must be a PDF: {pdf_path}')
    page.wait_for_timeout(1100)
    inputs=page.locator('input[type="file"]')
    def suitable_input():
        for i in range(inputs.count()):
            loc=inputs.nth(i)
            try:
                accept=loc.get_attribute('accept') or ''
                if not accept or '.pdf' in accept.lower() or 'application/pdf' in accept.lower() or '*' in accept:
                    return loc
            except Exception:continue
        return None
    selected_input=suitable_input()
    if selected_input is None:
        # The input can be dynamically inserted by ChatGPT's Add files menu.
        choices=[page.get_by_role('button',name=re.compile(r'attach|add files|add photos|upload',re.I)),
                 page.locator('button[data-testid="composer-plus-btn"]'),
                 page.locator('button[aria-label*="Add files" i]')]
        opened=False
        for candidates in choices:
            for i in range(min(candidates.count(),5)):
                button=candidates.nth(i)
                if selected(button):
                    button.click(timeout=8000);opened=True;break
            if opened:break
        if opened:
            page.wait_for_timeout(500)
            selected_input=suitable_input()
        if selected_input is None and opened:
            for item in [page.get_by_text(re.compile('Add photos & files|Upload from computer|Upload file',re.I)),
                         page.get_by_role('menuitem',name=re.compile('upload|file',re.I))]:
                if item.count():
                    try:
                        with page.expect_file_chooser(timeout=3000) as chooser:
                            item.first.click(timeout=5000)
                        chooser.value.set_files(str(pdf_path))
                        break
                    except Exception:continue
            else:
                selected_input=suitable_input()
        if selected_input is None and not opened:
            snapshot(page,diagnostics,'pdf_upload_control_missing')
            raise UIBlocked('ChatGPT file upload control missing. No topic requested.')
    if selected_input is not None:
        selected_input.set_input_files(str(pdf_path),timeout=25000)
    # Do not dispatch a paid/long ChatGPT request until attachment is visible as a chip.
    deadline=time.monotonic()+60
    while time.monotonic()<deadline:
        page.wait_for_timeout(700)
        try:
            body=page.locator('body').inner_text(timeout=5000)
            if pdf_path.name.casefold() in body.casefold():
                log(f'PDF attached and visible: {pdf_path.name}')
                return
        except Exception:pass
    snapshot(page,diagnostics,'pdf_attachment_unverified')
    raise UIBlocked(f'Could not verify PDF attached: {pdf_path.name}. No topic request sent.')

def resume_chat(context,url):
    for p in context.pages:
        if p.url.split('?')[0]==url.split('?')[0]:return p
    page=context.new_page();page.goto(url,wait_until='domcontentloaded',timeout=60000)
    return page

def chat_url(page):
    for _ in range(30):
        if '/c/' in page.url:return page.url.split('?')[0]
        time.sleep(.5)
    raise UIBlocked('ChatGPT conversation did not receive a permanent /c/ URL; stop to avoid losing project.')

def _click_label(page,pattern):
    r=re.compile(pattern,re.I)
    choices=[page.get_by_role('button',name=r),page.get_by_role('tab',name=r),page.get_by_text(r,exact=True)]
    for loc in choices:
        for i in range(min(loc.count(),6)):
            q=loc.nth(i)
            if selected(q):q.click(timeout=6000);return True
    return False

def _fill_field(page,value,selectors,scope=None):
    root=scope or page
    for sel in selectors:
        q=root.locator(sel)
        visible=[q.nth(i) for i in range(min(q.count(),10)) if selected(q.nth(i))]
        if len(visible)==1:
            visible[0].fill(value,timeout=12000);return True
    return False

def _click_first_visible(page, locators):
    for loc in locators:
        try:
            for i in range(min(loc.count(), 12)):
                item=loc.nth(i)
                if item.is_visible() and item.is_enabled():
                    item.click(timeout=7000)
                    return True
        except Exception:
            continue
    return False

def _speaker_enzo_selected(page):
    """Confirm from the visible closed Speaker 1 pill; don't trust menu text."""
    try:
        return bool(page.evaluate(r"""() => {
          const els=[...document.querySelectorAll('button,[role="button"]')];
          return els.some(el => {
            const t=(el.innerText||el.getAttribute('aria-label')||'').replace(/\s+/g,' ').trim();
            return /^Speaker\s*1\s*[-â€“â€”:]?\s*Koda\b/i.test(t) && el.getClientRects().length;
          });
        }""")) or selected(page.get_by_text(re.compile(r'^Speaker\s*1\s*[-â€“â€”:]?\s*Koda\b',re.I)))
    except Exception:
        return selected(page.get_by_text(re.compile(r'^Speaker\s*1\s*[-â€“â€”:]?\s*Koda\b',re.I)))

def _choose_enzo(page, diagnostics):
    # Playgrounds may retain the previous Koda choice across reloads.
    if _speaker_enzo_selected(page):
        log('AI Studio Speaker 1 already set to Koda; keeping existing selection')
        return
    pills=[page.get_by_role('button',name=re.compile(r'Speaker\s*1\s*[-â€“â€”:]',re.I)),
           page.get_by_text(re.compile(r'^Speaker\s*1\s*[-â€“â€”:]',re.I))]
    target=[page.get_by_role('option',name=re.compile(r'\bKoda\b',re.I)),
            page.get_by_role('menuitem',name=re.compile(r'\bKoda\b',re.I)),
            page.get_by_role('button',name=re.compile(r'\bKoda\b',re.I)),
            page.get_by_text(re.compile(r'^Koda(?:\s|$)',re.I))]
    for attempt in range(3):
        if _speaker_enzo_selected(page):
            log('AI Studio Speaker 1 confirmed: Koda')
            return
        # Re-open on each attempt; first visit may show a menu animation/loading.
        if not _click_first_visible(page,pills):
            page.wait_for_timeout(650)
            continue
        page.wait_for_timeout(850)
        # Try selecting directly BEFORE clicking the Storyteller heading, which may
        # collapse the voice menu in some AI Studio versions.
        if _click_first_visible(page,target):
            page.wait_for_timeout(550)
            if _speaker_enzo_selected(page):
                log('AI Studio Speaker 1 confirmed: Koda')
                return
        # Search field appears in some picker layouts.
        search=first_visible(page,['input[placeholder*="search" i]','input[aria-label*="search" i]'])
        if search is not None:
            try:
                search.fill('Koda',timeout=5000)
                page.wait_for_timeout(700)
            except Exception:pass
            if _click_first_visible(page,target):
                page.wait_for_timeout(550)
                if _speaker_enzo_selected(page):
                    log('AI Studio Speaker 1 confirmed: Koda')
                    return
        # Storyteller may be a category requiring expansion.
        _click_first_visible(page,[page.get_by_role('button',name=re.compile(r'^Storyteller$',re.I)),
                                   page.get_by_role('tab',name=re.compile(r'^Storyteller$',re.I)),
                                   page.get_by_text(re.compile(r'^Storyteller$',re.I),exact=True)])
        page.wait_for_timeout(650)
        if _click_first_visible(page,target):
            page.wait_for_timeout(550)
            if _speaker_enzo_selected(page):
                log('AI Studio Speaker 1 confirmed: Koda')
                return
        # Close any stale picker before next attempt.
        try:page.keyboard.press('Escape')
        except Exception:pass
        page.wait_for_timeout(450)
    snapshot(page,diagnostics,'studio_enzo_picker_failed')
    raise UIBlocked('Could not verify Storyteller / Koda after 3 picker attempts. No voice generation submitted. See studio_enzo_picker_failed.png and .txt in project diagnostics.')

def _visible_any(loc):
    """Unlike selected(), handle an invisible first duplicate matching node."""
    try:
        for i in range(min(loc.count(), 12)):
            if loc.nth(i).is_visible(timeout=1200):
                return True
    except Exception:
        pass
    return False


def _voice_drawer_open(page):
    """Detect the MODAL voice picker, not the always-visible settings summary.

    The right Run settings sidebar contains a permanent "Speaker settings"
    heading even AFTER the voice picker has closed. Treating that heading as an
    open drawer caused repeated false failures (confirmed by screenshot).
    """
    markers = [
        page.get_by_text('Current voice', exact=True),
        page.get_by_text('Audio Profile', exact=True),
        page.get_by_text("Director's note", exact=True),
        page.get_by_text('No voices match your search.', exact=True),
        page.get_by_text('All styles', exact=True),
        page.locator('input[placeholder*="Search 2,000" i]'),
    ]
    return any(_visible_any(m) for m in markers)


def _close_speaker_settings(page, diagnostics):
    """Dismiss an ACTUAL voice-picker drawer while leaving the sidebar alone."""
    if not _voice_drawer_open(page):
        log('AI Studio: voice picker already closed; main editor visible')
        return

    # The drawer header is at the TOP of the overlay; the normal sidebar
    # 'Speaker settings' summary is much lower. Never click its unrelated X.
    headings = page.get_by_text('Speaker settings', exact=True)
    header = None
    viewport = page.viewport_size or {}
    width = viewport.get('width', 0)
    for i in range(min(headings.count(), 12)):
        h = headings.nth(i)
        if not selected(h):
            continue
        b = h.bounding_box()
        if b and b['y'] < 145 and (not width or b['x'] > width * 0.45):
            header = b
            break

    if header is None:
        snapshot(page, diagnostics, 'studio_voice_picker_header_missing')
        raise UIBlocked('Voice picker open but top Speaker settings header missing; no generation submitted.')

    for _ in range(3):
        candidates = [
            page.get_by_role('button', name=re.compile(r'^Close$', re.I)),
            page.locator('button[aria-label*="close" i]'),
            page.get_by_text('close', exact=True),
        ]
        for locator in candidates:
            for i in range(min(locator.count(), 20)):
                btn = locator.nth(i)
                if not selected(btn):
                    continue
                b = btn.bounding_box()
                # Must be in top right area of the same overlay header.
                if not b or b['x'] <= header['x'] + 140 or abs(b['y'] - header['y']) > 65:
                    continue
                try:
                    btn.click(timeout=4000)
                    page.wait_for_timeout(300)
                    if not _voice_drawer_open(page):
                        log('AI Studio: closed voice-picker drawer')
                        return
                except Exception:
                    pass
        # Escape may dismiss the filter popover before the drawer, so retry.
        try:
            page.keyboard.press('Escape')
            page.wait_for_timeout(300)
        except Exception:
            pass
        if not _voice_drawer_open(page):
            log('AI Studio: dismissed voice-picker drawer')
            return

    snapshot(page, diagnostics, 'studio_voice_picker_stuck')
    raise UIBlocked('Voice picker is still open. See studio_voice_picker_stuck.png; no generation submitted.')


def _set_studio_style(page, diagnostics):
    """Use the MAIN narration Style popover, not voice-library Style filter."""
    _close_speaker_settings(page, diagnostics)
    if not _speaker_enzo_selected(page):
        snapshot(page,diagnostics,'studio_enzo_after_drawer')
        raise UIBlocked('Koda was not confirmed after leaving Speaker settings; no generation submitted.')
    hint=re.compile(r'Describe the voice style|Tone, mood, pace',re.I)
    def visible_hint():
        for loc in [page.get_by_text(hint),page.locator('textarea[placeholder*="voice style" i]'),
                    page.locator('[placeholder*="voice style" i]'),
                    page.locator('[data-placeholder*="voice style" i]')]:
            for i in range(min(loc.count(),12)):
                q=loc.nth(i)
                if selected(q):return q
        return None

    if visible_hint() is None:
        # The correct Style button is in the MAIN editor alongside Speaker 1.
        # The library's filter also says Style, so it must be CLOSED first.
        # AI Studio uses non-button divs for some toolbar controls. Use
        # the Koda pill as an anchor, and require the MAIN Style control to
        # be beside it on the SAME ROW. Avoid voice-filter Style entirely.
        pill_locators=[page.get_by_role('button',name=re.compile(r'Speaker\s*1\s*[-â€“â€”:]\s*Koda',re.I)),
                       page.get_by_text(re.compile(r'Speaker\s*1\s*[-â€“â€”:]\s*Koda',re.I))]
        pill_box=None
        for pill in pill_locators:
            for i in range(min(pill.count(),10)):
                if selected(pill.nth(i)):
                    pill_box=pill.nth(i).bounding_box()
                    if pill_box:break
            if pill_box:break
        if not pill_box:
            snapshot(page,diagnostics,'studio_main_speaker_pill_missing')
            raise UIBlocked('Cannot find Koda speaker pill to identify main Style button.')
        locators=[page.get_by_role('button',name='Style',exact=True),
                  page.get_by_text('Style',exact=True)]
        candidates=[]
        for loc in locators:
            for i in range(min(loc.count(),12)):
                q=loc.nth(i)
                if not selected(q):continue
                b=q.bounding_box()
                if not b:continue
                if (pill_box['x'] + 120 <= b['x'] <= pill_box['x'] + 460 and
                    abs(b['y']-pill_box['y']) < 75):
                    candidates.append((abs(b['y']-pill_box['y']),abs(b['x']-pill_box['x']-240),q))
            if candidates:break
        if not candidates:
            snapshot(page,diagnostics,'studio_main_style_ambiguous')
            raise UIBlocked('Main Style control beside Speaker 1 - Koda was not found.')
        candidates.sort(key=lambda t:(t[0],t[1]))
        candidates[0][2].click(timeout=7000)
        page.wait_for_timeout(650)
    field=first_visible(page,[
        'textarea[placeholder*="voice style" i]',
        'input[placeholder*="voice style" i]',
        'textarea[placeholder*="Tone, mood" i]',
        '[contenteditable="true"][data-placeholder*="voice style" i]',
        '[role="textbox"][aria-label*="voice style" i]'])
    if field is None:
        hint_el=visible_hint()
        if hint_el is None:
            snapshot(page,diagnostics,'studio_main_style_missing')
            raise UIBlocked('Main-editor Style popover opened but custom style field not visible. No generation submitted.')
        try:
            hint_el.click(timeout=5000)
        except Exception:pass
        page.wait_for_timeout(200)
        focused=page.locator(':focus')
        if focused.count():
            try:
                node=focused.first
                editable=node.evaluate('e=>e.matches("textarea,input,[contenteditable=true],[role=textbox]")')
                if editable:field=node
            except Exception:pass
    if field is None:
        snapshot(page,diagnostics,'studio_main_style_no_input')
        raise UIBlocked('Main editor style text field not editable; no generation submitted.')
    try:field.fill(STYLE,timeout=8000)
    except Exception as exc:
        snapshot(page,diagnostics,'studio_main_style_fill_error')
        raise UIBlocked(f'Could not fill main-editor style text: {exc}')
    actual=field.evaluate("el => ('value' in el ? el.value : el.innerText || '').trim()")
    if actual!=STYLE:
        snapshot(page,diagnostics,'studio_main_style_verify_failed')
        raise UIBlocked('Documentary style text did not match after filling; no generation submitted.')
    page.keyboard.press('Tab')
    log('AI Studio custom documentary style entered and verified in main editor')

def studio_prepare(page,diagnostics):
    if 'generate-speech' not in page.url:
        page.goto(AI_STUDIO_URL,wait_until='domcontentloaded',timeout=60000)
    page.wait_for_timeout(1400)
    if re.search(r'accounts.google.com|signin',page.url,re.I):
        snapshot(page,diagnostics,'studio_login')
        raise UIBlocked('Sign in to Google AI Studio in this Chrome window and rerun.')

    # Two screens occur at the SAME URL: the initial gallery shows
    # "Create new dialog" and "Create new voice"; only after clicking the
    # former does the editor show "Speaker 1 - Fola" and "Style".
    speaker_rx=re.compile(r'Speaker\s*1\s*[-â€“â€”]\s*',re.I)
    def has_editor():
        return (selected(page.get_by_role('button',name=speaker_rx)) or
                selected(page.get_by_text(speaker_rx)))
    if not has_editor():
        entry=[page.get_by_role('button',name=re.compile(r'^Create new dialog(?:ue)?$',re.I)),
               page.get_by_text(re.compile(r'^Create new dialog(?:ue)?$',re.I),exact=True),
               page.get_by_role('button',name=re.compile(r'Create new dialog',re.I))]
        # Never click "Create new voice"; that is a different UI workflow.
        if not _click_first_visible(page,entry):
            snapshot(page,diagnostics,'studio_create_new_dialog_missing')
            raise UIBlocked('Neither Create new dialog nor Speaker 1 is visible in AI Studio. No generation submitted.')
        log('AI Studio: opened Create new dialog editor')
        try:
            page.get_by_text(speaker_rx).first.wait_for(state='visible',timeout=15000)
        except Exception:
            if not has_editor():
                snapshot(page,diagnostics,'studio_dialog_editor_missing')
                raise UIBlocked('Clicked Create new dialog but Speaker 1 editor did not load. No generation submitted.')
    if not has_editor():
        snapshot(page,diagnostics,'studio_playground_missing')
        raise UIBlocked('Speech editor / Speaker 1 control missing. No generation submitted.')
    _choose_enzo(page, diagnostics)
    _set_studio_style(page, diagnostics)

def _narration_editor_rect(page):
    """Bounding-box anchor: the actual Speaker 1 pill in the center editor.

    Do not anchor to the right-hand Speaker settings summary or popovers.
    """
    rx=re.compile(r'^Speaker\s*1\s*[-â€“â€”:]\s*Koda\b',re.I)
    for group in [page.get_by_role('button',name=rx),page.get_by_text(rx)]:
        for i in range(min(group.count(),12)):
            item=group.nth(i)
            if selected(item):
                box=item.bounding_box()
                if box and box['width']>70 and box['height']>12:
                    return box
    return None


def _narration_candidates(page, speaker):
    """Return distinctly ranked, visible editing surfaces inside the speech block.

    Gemini's current UI displays `<gasp> That's a great idea!` as placeholder
    text, so body.innerText doesn't include it. Earlier selectors missed this.
    """
    selectors=[
        'textarea[placeholder*="gasp" i]',
        'textarea[placeholder*="great idea" i]',
        '[placeholder*="gasp" i]',
        '[placeholder*="great idea" i]',
        '[data-placeholder*="gasp" i]',
        '[data-placeholder*="great idea" i]',
        '[aria-label*="speech text" i]',
        '[aria-label*="narration" i]',
        '[aria-label*="script" i]',
        '[data-testid*="speech-editor" i]',
        '[data-slate-editor="true"]',
        '[data-lexical-editor="true"]',
        '.ProseMirror[contenteditable="true"]',
        '.tiptap[contenteditable="true"]',
        'textarea', '[role="textbox"]', '[contenteditable="true"]'
    ]
    # The real editing area is BELOW Speaker 1 â€“ Koda and to its right/under it.
    # Exclude both the right sidebar and the short Style popup textarea.
    vp=page.viewport_size or {'width':1750,'height':950}
    central_right=max(speaker['x']+460, vp['width']*.765)
    results={}
    for index,selector in enumerate(selectors):
        items=page.locator(selector)
        for i in range(min(items.count(),25)):
            node=items.nth(i)
            try:
                if not node.is_visible(timeout=800):continue
                rect=node.bounding_box()
                if not rect:continue
                cx=rect['x']+rect['width']/2
                cy=rect['y']+rect['height']/2
                if cx<speaker['x']-70 or cx>central_right:continue
                if cy<speaker['y']+speaker['height']-4 or cy>speaker['y']+500:continue
                # A large narration editor or a textbox with the exact sample
                # placeholder; never select the small custom Style field.
                info=node.evaluate('''el => ({
                    tag:el.tagName.toLowerCase(),
                    role:el.getAttribute('role')||'',
                    placeholder:el.getAttribute('placeholder')||el.getAttribute('data-placeholder')||'',
                    label:el.getAttribute('aria-label')||'',
                    editable:el.isContentEditable,
                    disabled:!!el.disabled,
                    outer:el.outerHTML.slice(0,250)
                })''')
                if info['disabled']:continue
                hint=(info['placeholder']+' '+info['label']).lower()
                if 'voice style' in hint or 'tone, mood' in hint:
                    continue  # This is the custom Style popover, never the script.
                explicit=('gasp' in hint or 'great idea' in hint or 'speech text' in hint or
                          'narration' in hint or 'script' in hint)
                if (rect['width']<400 or rect['height']<35) and not explicit:continue
                if not (info['tag'] in ('textarea','input') or info['editable'] or info['role']=='textbox'):
                    continue
                score=(70 if explicit else 0)+(35 if rect['width']>=400 else 0)+\
                    (15 if rect['height']>=60 else 0)+(10 if index<10 else 0)
                # Dedupe selectors which resolve the same DOM element.
                key=(round(rect['x']),round(rect['y']),round(rect['width']),round(rect['height']),info['tag'])
                if key not in results or score>results[key][0]:
                    results[key]=(score,node,rect,info)
            except Exception:continue
    return sorted(results.values(),key=lambda p:p[0],reverse=True)


def _narration_value(node):
    return node.evaluate('''e => {
        if ('value' in e) return e.value;
        return (e.innerText||e.textContent||'').trim();
    }''')


def _narration_confirm(actual, expected):
    # Compare exact words allowing browser editor to normalise paragraph breaks.
    norm=lambda x:re.sub(r'\s+',' ',x or '').strip()
    return norm(actual)==norm(expected)


def _editor_diagnostics(page,diagnostics,part,reason):
    """Record textbox attributes and locations, not browser/account secrets."""
    snapshot(page,diagnostics,f'studio_part{part}_text_missing')
    try:
        info=page.evaluate('''() => [...document.querySelectorAll('textarea,input,[role="textbox"],[contenteditable], [data-slate-editor], [data-lexical-editor]')]
          .filter(e=>e.getClientRects().length).slice(0,65).map(e=>{
             const r=e.getBoundingClientRect();return {
             tag:e.tagName.toLowerCase(), role:e.getAttribute('role'),
             placeholder:e.getAttribute('placeholder')||e.getAttribute('data-placeholder'),
             aria:e.getAttribute('aria-label'),editable:e.getAttribute('contenteditable'),
             rect:{x:r.x,y:r.y,width:r.width,height:r.height},
             html:e.outerHTML.slice(0,400).replace(/value="[^"]+"/g,'value="[redacted]"')};
          })''')
        import json
        Path(diagnostics).mkdir(parents=True,exist_ok=True)
        (Path(diagnostics)/f'studio_part{part}_editor_dom.json').write_text(json.dumps({'reason':reason,'elements':info},indent=2),encoding='utf-8')
    except Exception:pass


def _fill_narration_script(page,text,diagnostics,part):
    """Fill the *main* spoken narration field and verify before pressing Run.

    No submission is attempted here. Do not guess based solely on first textarea:
    the Style popover and voice-picker search also contain editable fields.
    """
    speaker=_narration_editor_rect(page)
    if not speaker:
        _editor_diagnostics(page,diagnostics,part,'Speaker 1 â€“ Koda pill missing')
        raise UIBlocked('Cannot anchor the narration text editor to Speaker 1 â€“ Koda. No generation submitted.')
    candidates=_narration_candidates(page,speaker)
    if candidates:
        best=candidates[0]
        # Equal strong candidates are ambiguous: don't write to the wrong field.
        if len(candidates)==1 or best[0]>=candidates[1][0]+15:
            node=best[1]
            try:
                node.fill(text,timeout=24000)
                actual=_narration_value(node)
                if _narration_confirm(actual,text):
                    log(f'AI Studio narration Part {part} entered and verified ({len(text.split())} words)')
                    return
            except Exception:pass
    # UI implementations can create the textarea only after the user clicks
    # inside the speech card. Locate the content area RELATIVE to the visible
    # speaker pill; then require that a genuine editable element gained focus.
    try:
        page.mouse.click(speaker['x']+min(75,speaker['width']/3),
                         speaker['y']+speaker['height']+65)
        page.wait_for_timeout(300)
        active=page.locator(':focus')
        if active.count():
            node=active.first
            is_editable=node.evaluate('''e=>e.matches('textarea,input,[contenteditable="true"],[role="textbox"]')||e.isContentEditable''')
            if is_editable:
                bbox=node.bounding_box()
                vp=page.viewport_size or {'width':1750}
                if (bbox and bbox['x']<vp['width']*.77 and bbox['y']>speaker['y'] and
                    bbox['width']>=200):
                    try:
                        node.fill(text,timeout=24000)
                    except Exception:
                        node.click();page.keyboard.press('ControlOrMeta+A');page.keyboard.insert_text(text)
                    if _narration_confirm(_narration_value(node),text):
                        log(f'AI Studio narration Part {part} entered and verified ({len(text.split())} words)')
                        return
    except Exception:pass
    _editor_diagnostics(page,diagnostics,part,'No uniquely identifiable main narration editor or verification failed')
    raise UIBlocked(f'Cannot identify and verify AI Studio narration editor for Part {part}; see studio_part{part}_editor_dom.json. No generation submitted.')


def studio_make_part(page,text,target,diagnostics,part,mark_submitted):
    """Generate ONE part and save browser download. Does not re-click Generate after uncertainty."""
    target=Path(target)
    if target.is_file() and target.stat().st_size>10000:
        log('Existing audio verified; skipping '+target.name);return
    studio_prepare(page,diagnostics)
    _fill_narration_script(page,text,diagnostics,part)
    if not _speaker_enzo_selected(page):
        snapshot(page,diagnostics,f'studio_part{part}_voice_changed')
        raise UIBlocked('Speaker 1 is not confirmed as Koda. No generation submitted.')
    run_buttons=[page.get_by_role('button',name=re.compile(r'^Run(?:\s|$)',re.I)),
                 page.get_by_role('button',name=re.compile(r'^Generate(?: speech| audio)?$',re.I))]
    run=None
    for loc in run_buttons:
        for i in range(min(loc.count(),8)):
            x=loc.nth(i)
            try:
                if x.is_visible() and x.is_enabled():run=x;break
            except Exception:pass
        if run:break
    if run is None:
        snapshot(page,diagnostics,f'studio_part{part}_run_missing')
        raise UIBlocked('Run button unavailable. No voice request was submitted.')
    # Record uncertainty immediately BEFORE clicking: if Run succeeds but the
    # download fails, never automatically generate the same part again.
    mark_submitted()
    run.click(timeout=12000)
    log(f'AI Studio generation started for Part {part}; waiting for download...')
    return studio_wait_for_audio(page,target,diagnostics,part)


def studio_wait_for_audio(page,target,diagnostics,part,max_wait_seconds=2700):
    """Wait for the already submitted TTS result. Never clicks Run/Generate."""
    target=Path(target)
    start=time.monotonic()
    next_log=start
    last_click=0.0
    click_errors=0
    while time.monotonic()-start<max_wait_seconds:
        now=time.monotonic()
        if now>=next_log:
            log(f'AI Studio Part {part}: still waiting for audio ({int(now-start)}s elapsed). No regeneration.')
            next_log=now+30
        # Google can display a Download button before it becomes actionable.
        # A short Playwright timeout is not evidence that generation failed.
        candidates=[page.get_by_role('button',name=re.compile(r'^Download(?: audio| file| wav)?$',re.I)),
                    page.get_by_text(re.compile(r'^Download(?: audio)?$',re.I),exact=True),
                    page.locator('[aria-label*="download" i]')]
        ready=[]
        for loc in candidates:
            try:
                for i in range(min(loc.count(),8)):
                    el=loc.nth(i)
                    if el.is_visible(timeout=900) and el.is_enabled(timeout=900):
                        ready.append(el)
            except Exception:pass
            if ready:break
        if ready and now-last_click>=25:
            last_click=now
            try:
                with page.expect_download(timeout=12000) as info:
                    ready[-1].click(timeout=7000)
                download=info.value
                suffix=Path(download.suggested_filename).suffix.lower()
                if suffix not in ('.wav','.mp3','.m4a','.ogg','.webm'):
                    raise UIBlocked(f'Unexpected downloaded format {suffix}; not saving as audio.')
                dest=target.with_suffix(suffix)
                dest.parent.mkdir(parents=True,exist_ok=True)
                download.save_as(str(dest))
                if dest.stat().st_size<10000:
                    dest.unlink(missing_ok=True)
                    raise UIBlocked('Audio download is unexpectedly small.')
                log(f'Saved Part {part}: {dest}')
                return dest
            except Exception as exc:
                click_errors+=1
                log(f'Part {part}: download not ready (attempt {click_errors}: {str(exc)[:110]}). Continuing to wait.')
        time.sleep(5)
    snapshot(page,diagnostics,f'studio_part{part}_still_waiting')
    raise UIBlocked(f'Part {part} was submitted but no verified audio download appeared within {max_wait_seconds//60} minutes. '
                    'No duplicate generation sent; leave AI Studio tab open and recover the existing result.')

