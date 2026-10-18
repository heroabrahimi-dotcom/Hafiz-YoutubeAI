"""ChatGPT -> Google Flow browser automation. No image API, no credential handling.

Safety: only submits generation after positively identifying 'Generate Image'.
On an uncertain submission, the journal blocks automatic retries to avoid credit waste.
"""
from __future__ import annotations

import argparse
import os
import hashlib
import json
import re
import sys
import time

os.environ.setdefault('PYTHONUTF8', '1')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8:replace')
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, 'reconfigure'):
        try: _stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception: pass
from datetime import datetime
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from journal import Journal
from prompts import extract_from_messages, verify_sequence

BASE = Path(__file__).resolve().parent
CONFIG = json.loads((BASE / 'config.json').read_text(encoding='utf-8'))
OUTPUT = Path(CONFIG['output_dir'])
WORK = Path(CONFIG['working_dir'])
FLOW_URL = CONFIG['flow_url']
IMAGE_TIMEOUT = int(CONFIG.get('generation_timeout_seconds', 240))
DOWNLOAD_TIMEOUT = int(CONFIG.get('download_timeout_seconds', 210))
QUALITY_TIMEOUT = int(CONFIG.get('upscale_timeout_seconds', 240))



# Additional text is applied only when sending the prompt to Flow.
# Journal hashes continue to use the original extracted prompt so the
# resume behavior and Auto NEXT 5 loop stay compatible.
SINGLE_SCENE_RULES = (
    "OUTPUT REQUIREMENTS: Make EXACTLY ONE independent, continuous, "
    "full-bleed 16:9 illustration, filling the entire canvas edge-to-edge. "
    "Depict only the single scene described above. "
    "No storyboard, no comic strip, no triptych, no collage, no montage, "
    "no multiple panels, no split screen, no inset images, no borders, "
    "no frames, no white margins, no captions, no labels, no numbering, "
    "no typography, and no watermarks. Do not write VISUAL or any text "
    "into the picture. Keep the described cartoon style consistent."
)


def flow_prompt_text(raw_prompt):
    """One visual at a time, with layout restrictions appended."""
    raw_prompt = raw_prompt.strip()
    if not raw_prompt:
        raise ValueError('Empty image prompt')
    return raw_prompt + "\n\n" + SINGLE_SCENE_RULES


def say(message):
    print(f'[{datetime.now().strftime("%H:%M:%S")}] {message}', flush=True)


def fail(message):
    raise RuntimeError(message)


def visible(locator):
    try:
        return locator.is_visible(timeout=500) and locator.is_enabled(timeout=500)
    except Exception:
        return False


def first_visible(candidates):
    for loc in candidates:
        try:
            for i in range(min(loc.count(), 12)):
                item = loc.nth(i)
                if visible(item):
                    return item
        except Exception:
            continue
    return None


def capture_diagnostics(page, label):
    """Local-only files; may contain private on-screen content."""
    dest = WORK / 'diagnostics'
    dest.mkdir(parents=True, exist_ok=True)
    name = f'{datetime.now().strftime("%Y%m%d_%H%M%S")}_{label}'
    try:
        page.screenshot(path=str(dest / f'{name}.png'), full_page=False, timeout=15000)
    except Exception as exc:
        say(f'Diagnostic screenshot unavailable: {exc}')
    try:
        info = page.evaluate('''() => ({
            url: location.href,
            title: document.title,
            buttons: Array.from(document.querySelectorAll('button,[role="button"],[role="menuitem"], [role="option"]'))
              .filter(e => e.getBoundingClientRect().width && e.getBoundingClientRect().height)
              .slice(0, 180)
              .map(e => ({tag:e.tagName, text:(e.innerText||'').slice(0,110),
                          label:e.getAttribute('aria-label'), title:e.getAttribute('title'),
                          selected:e.getAttribute('aria-selected')})),
            inputs: Array.from(document.querySelectorAll('textarea,input,[contenteditable="true"]'))
              .slice(0,40).map(e=>({tag:e.tagName,placeholder:e.getAttribute('placeholder'),
                                    role:e.getAttribute('role'), contenteditable:e.getAttribute('contenteditable')}))
        })''')
        (dest / f'{name}.json').write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception as exc:
        say(f'Diagnostic DOM unavailable: {exc}')
    say(f'Saved local diagnostic information: {dest / name}.*')


def read_latest_assistant_with_pc_bot_fallback(page):
    """Adapted from the user's read_chatgpt.py: assistant-role -> h4 heading -> turn.

    Evaluates DOM in one browser call; never sends messages or executes PCBot tasks.
    """
    return page.evaluate(r"""() => {
      const clean = s => (s || '').replace(/\u00a0/g, ' ').trim();
      const assistants = Array.from(document.querySelectorAll('[data-message-author-role="assistant"]'));
      for (let i=assistants.length-1; i>=0; i--) {
        const text=clean(assistants[i].innerText);
        if (text) return {latest:text,method:'assistant-role',counts:{assistants:assistants.length}};
      }
      const headings=Array.from(document.querySelectorAll('h4'));
      for (let i=headings.length-1; i>=0; i--) {
        const h=headings[i];
        if (clean(h.innerText) !== 'ChatGPT said:') continue;
        let node=h.parentElement;
        for (let depth=0; depth<8 && node; depth++,node=node.parentElement) {
          const text=clean(node.innerText);
          // The smallest containing block that includes some answer text.
          if (text.startsWith('ChatGPT said:') && text.length > 24) {
            return {latest:clean(text.replace(/^ChatGPT said:\s*/i,'')),
                    method:'chatgpt-heading',counts:{headings:headings.length}};
          }
        }
      }
      const turns=Array.from(document.querySelectorAll('[data-testid^="conversation-turn-"]'));
      for (let i=turns.length-1; i>=Math.max(0,turns.length-20); i--) {
        const text=clean(turns[i].innerText);
        if (text.startsWith('ChatGPT said:') && text.length>24)
          return {latest:clean(text.replace(/^ChatGPT said:\s*/i,'')),
                  method:'turn-heading',counts:{turns:turns.length}};
      }
      return {latest:'',method:'none',counts:{assistants:assistants.length,
        headings:headings.length,turns:turns.length,bodyTextLength:(document.body?.innerText||'').length,
        title:document.title,url:location.href}};
    }""")


def find_chatgpt_prompts(context):
    matches=[]
    required_url=os.getenv('PIPELINE_CHAT_URL', '').split('?')[0]
    for page in context.pages:
        if required_url and page.url.split('?')[0] != required_url:
            continue
        if not re.search(r'https://(chatgpt\.com|chat\.openai\.com)/', page.url):
            continue
        try:
            data=read_latest_assistant_with_pc_bot_fallback(page)
            text=(data.get('latest') or '').strip()
            say(f"ChatGPT reader method={data.get('method')}; characters={len(text)}; page={page.url}")
            if not text:
                say('ChatGPT DOM counts: '+json.dumps(data.get('counts',{})))
                capture_diagnostics(page,'chatgpt_pc_bot_reader_failed')
                continue
            prompts=extract_from_messages([text])
            if prompts:
                verify_sequence(prompts)
                matches.append((page,prompts))
            else:
                say('Latest assistant reply was read, but contains no numbered VISUAL prompts. '+
                    'Make sure the most recent assistant reply contains Visual 1 / Prompt: ...')
        except ValueError:
            raise
        except Exception as exc:
            say(f'Could not read ChatGPT tab {page.url}: {exc}')
            capture_diagnostics(page,'chatgpt_pc_bot_reader_error')
    if not matches:
        fail('No numbered image prompts found in the latest assistant reply. '+
             'Review scan output/diagnostics; do not regenerate images yet.')
    if len(matches)>1:
        fail('Multiple ChatGPT tabs contain prompts; close extras before generating.')
    page,prompts=matches[0]
    say(f'ChatGPT source: {page.url}')
    say(f'Found {len(prompts)} consecutive image prompts ({prompts[0].number}..{prompts[-1].number}).')
    return prompts

def find_flow_page(context):
    # Avoid using an unrelated project tab; navigate ONLY to the supplied project.
    for page in context.pages:
        if page.url.startswith(FLOW_URL):
            return page
    for page in context.pages:
        if page.url.startswith('https://flow.google.com/'):
            page.goto(FLOW_URL, wait_until='domcontentloaded', timeout=45000)
            return page
    page = context.new_page()
    page.goto(FLOW_URL, wait_until='domcontentloaded', timeout=45000)
    return page


def find_prompt_input(page):
    candidates = [
        page.locator('textarea[placeholder*="prompt" i]'),
        page.locator('[role="textbox"][contenteditable="true"]'),
        page.locator('[contenteditable="true"][data-placeholder]'),
        page.locator('textarea'),
        page.locator('[contenteditable="true"]'),
        page.get_by_role('textbox'),
    ]
    # Prefer editor in the main visible page. If multiple plausible boxes, fail instead of typing somewhere random.
    selections = []
    for loc in candidates:
        try:
            entries = [loc.nth(i) for i in range(min(loc.count(), 20)) if visible(loc.nth(i))]
            if len(entries) == 1:
                return entries[0]
            if entries:
                selections.extend(entries)
        except Exception:
            pass
    fail(f'Flow prompt box not identified uniquely (found {len(selections)} possible fields).')


def choose_image_mode(page):
    """Verify the selected Flow image model without mistaking an icon for its label.

    Some Flow builds expose only the Material icon text ``settings_2`` on the
    Settings trigger. In that case inspect the trigger's local context and
    selected model controls, and only open the Settings trigger to inspect it
    if the selected model is otherwise not visible. Never click a model choice.
    """
    settings = first_visible([
        page.get_by_role('button', name='Settings trigger', exact=True),
        page.locator('button[aria-label="Settings trigger"]'),
    ])
    if not settings:
        fail('Flow model Settings trigger is missing; cannot verify image mode.')

    def trigger_text(loc):
        bits = []
        for getter in (
            lambda: loc.inner_text(timeout=2500),
            lambda: loc.get_attribute('aria-label'),
            lambda: loc.get_attribute('title'),
        ):
            try:
                value = getter()
                if value:
                    bits.append(str(value).strip())
            except Exception:
                pass
        return ' / '.join(dict.fromkeys(x for x in bits if x))

    def inspect_mode_context():
        """Read only visible, local settings context and selected model controls."""
        try:
            return page.evaluate(r"""() => {
              const clean = e => ((e && (e.innerText || e.textContent)) || '')
                .replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim();
              const visible = e => {
                if (!e) return false;
                const r = e.getBoundingClientRect();
                const s = getComputedStyle(e);
                return r.width > 0 && r.height > 0 && s.display !== 'none' &&
                  s.visibility !== 'hidden' && Number(s.opacity || 1) > 0;
              };
              const compact = (e, source) => ({source, text: clean(e).slice(0, 500)});
              const contexts = [];
              const trigger = document.querySelector('button[aria-label="Settings trigger"]');
              if (trigger) {
                let n = trigger;
                for (let d = 0; n && d < 5; d++, n = n.parentElement) {
                  if (visible(n)) contexts.push(compact(n, 'settings ancestor ' + d));
                }
              }
              const selectedSelector = [
                '[aria-selected="true"]', '[aria-checked="true"]',
                '[aria-pressed="true"]', '[data-state="checked"]',
                'input[type="radio"]:checked', '.mat-mdc-radio-checked',
                '.mdc-radio--checked', '.mat-mdc-option.mat-mdc-selected'
              ].join(',');
              const selected = Array.from(document.querySelectorAll(selectedSelector))
                .filter(visible).slice(0, 80).map((e, i) => {
                  let n = e, txt = clean(e);
                  for (let d = 0; n && d < 4; d++, n = n.parentElement) {
                    const t = clean(n);
                    if (t && t.length <= 400 && /nano\s*banana|\bveo\b|video generation/i.test(t)) {
                      txt = t; break;
                    }
                  }
                  return {source: 'selected control ' + i, text: txt.slice(0, 500)};
                });
              const panels = Array.from(document.querySelectorAll(
                '[role="dialog"], [role="menu"], [role="listbox"], .cdk-overlay-pane,' +
                'mat-dialog-container, .mat-mdc-menu-panel, [data-testid*="setting" i]'))
                .filter(visible).map((e, i) => compact(e, 'settings panel ' + i))
                .filter(x => /nano\s*banana|\bveo\b|video generation/i.test(x.text))
                .slice(0, 20);
              const exactLabels = Array.from(document.querySelectorAll(
                'button,[role="button"],[role="option"],[role="radio"],label,span,div'))
                .filter(visible).map(e => ({e, text: clean(e)}))
                .filter(x => x.text.length <= 90 &&
                  /^\s*(?:ðŸŒ\s*)?nano\s*banana(?:\s*2(?:\.1)?)?(?:\s*[Â·|/].*)?\s*$/i.test(x.text))
                .filter(x => !Array.from(x.e.children || []).some(c => clean(c) === x.text))
                .map((x, i) => ({source: 'visible model label ' + i, text: x.text}))
                .slice(0, 20);
              const visibleVeo = Array.from(document.querySelectorAll(
                'button,[role="button"],[role="option"],[role="radio"],label,span,div'))
                .filter(visible).map(clean).filter(t => t.length <= 90 &&
                  /^\s*(?:veo(?:\s*\d+(?:\.\d+)?)?|video generation)\s*$/i.test(t));
              return {contexts, selected, panels, exactLabels, visibleVeoCount: visibleVeo.length};
            }""")
        except Exception:
            return {'contexts': [], 'selected': [], 'panels': [], 'exactLabels': [], 'visibleVeoCount': 0}

    def is_nano(text):
        return bool(re.search(r'Nano\s*Banana', text or '', re.I))

    def is_video(text):
        return bool(re.search(r'\bVeo\b|video generation', text or '', re.I))

    def choose_evidence(info, direct_text=''):
        # The trigger itself is strongest when it actually includes a model name.
        if is_nano(direct_text) and not is_video(direct_text):
            return direct_text
        # A selected radio/option/toggle is stronger than an unselected menu label.
        for item in info.get('selected', []):
            text = item.get('text', '')
            if is_nano(text) and not is_video(text):
                return text
        # Prefer the nearest short context around the settings trigger.
        for item in info.get('contexts', []):
            text = item.get('text', '')
            if is_nano(text) and not is_video(text) and len(text) <= 300:
                return text
        # If a model chooser is open, accept a unique Nano Banana label only when
        # no Veo/video alternative is visible. Otherwise the selection is ambiguous.
        labels = [x.get('text', '') for x in info.get('exactLabels', []) if is_nano(x.get('text', ''))]
        if labels and not info.get('visibleVeoCount') and len(set(labels)) == 1:
            return labels[0]
        for item in info.get('panels', []):
            text = item.get('text', '')
            if is_nano(text) and not is_video(text) and len(text) <= 250:
                return text
        return ''

    direct = trigger_text(settings)
    info = inspect_mode_context()
    model_text = choose_evidence(info, direct)

    # ``settings_2`` is a Material icon ligature, not a model. Open only the
    # dedicated Settings trigger to expose the selected option; never click an
    # option, model card, or generic submit control during verification.
    if not model_text:
        try:
            settings.click(timeout=4000)
            page.wait_for_timeout(500)
            info = inspect_mode_context()
            model_text = choose_evidence(info, trigger_text(settings))
        except Exception:
            pass

    if not model_text:
        detail = direct[:100] if direct else '(empty settings trigger label)'
        fail('Could not verify the selected Nano Banana image model. The Settings trigger '
             f'exposed {detail!r}, but no unambiguous selected Nano Banana option was found. '
             'No submission was made; check the model selector in Flow.')
    if is_video(model_text):
        fail(f'Flow model may be in video mode: {model_text[:100]!r}. No submission.')

    # Close any settings/menu overlay before locating the prompt and submit button.
    try:
        page.keyboard.press('Escape')
        page.wait_for_timeout(200)
    except Exception:
        pass

    generate = None
    for candidate in [
        page.locator('button[aria-label="Start generation"]'),
        page.get_by_role('button', name='Start generation', exact=True),
    ]:
        try:
            for i in range(min(candidate.count(), 12)):
                item = candidate.nth(i)
                if item.is_visible():
                    generate = item
                    break
        except Exception:
            pass
        if generate is not None:
            break
    if generate is None:
        fail('Nano Banana image mode confirmed, but Start generation button not found in DOM.')
    clean_model_text = re.sub(r'\s+', ' ', model_text).strip()
    say(f'Flow IMAGE mode verified from selected settings context: {clean_model_text[:160]}. '
        'Note: x2 in this setting refers to output count, NOT upscale.')
    return generate


def image_fingerprints(page):
    """Visible gallery images only. Never downloads a pre-existing gallery image."""
    return page.evaluate('''() => Array.from(document.images).flatMap((im, index) => {
      const r=im.getBoundingClientRect(), s=getComputedStyle(im);
      if (!im.complete || im.naturalWidth < 350 || im.naturalHeight < 350 ||
          r.width < 100 || r.height < 80 || s.display==='none' || s.visibility==='hidden') return [];
      const key=im.currentSrc || im.src;
      return key ? [{index, key, width:im.naturalWidth, height:im.naturalHeight}] : [];
    })''')


def new_image_after_submit(page, before, timeout_seconds):
    before_set = {it['key'] for it in before}
    until = time.monotonic() + timeout_seconds
    first_seen = None
    stable_count = 0
    last_key = None
    while time.monotonic() < until:
        current = image_fingerprints(page)
        fresh = [it for it in current if it['key'] not in before_set]
        if fresh:
            # Latest DOM gallery item; requires same image visible across 3 polls.
            candidate = fresh[-1]
            if candidate['key'] == last_key:
                stable_count += 1
            else:
                last_key = candidate['key']
                stable_count = 1
                first_seen = time.monotonic()
            if stable_count >= 3 and time.monotonic() - first_seen >= 2.5:
                return candidate
        page.wait_for_timeout(1200)  # polling for observable changes, not a blind generation timer
    fail('No NEW completed image was identifiable in Flow before the generation timeout. '
         'Submission may have succeeded; do not rerun this visual until manually checked.')


def find_image_locator(page, candidate):
    try:
        images = page.locator('img')
        for i in range(images.count()):
            item = images.nth(i)
            src = item.evaluate('(el) => el.currentSrc || el.src')
            if src == candidate['key']:
                return item
    except Exception:
        pass
    fail('The new image disappeared or moved before downloading. Stopping to avoid the wrong file.')


def image_scopes(image_locator):
    result = []
    for depth in range(1, 9):
        try:
            parent = image_locator.locator('xpath=' + '/'.join(['..'] * depth))
            if parent.count() == 1:
                buttons = parent.locator('button,[role="button"]')
                # Stop at the local image card; do not accidentally select a
                # download control belonging to another generated image.
                if 0 < buttons.count() <= 24 and parent.locator('img').count() <= 3:
                    result.append(parent)
        except Exception:
            continue
    return result


def find_control(page, scope_list, pattern):
    regex = re.compile(pattern, re.I)
    for scope in scope_list + [page]:
        item = first_visible([
            scope.get_by_role('button', name=regex),
            scope.locator('button[title]').filter(has=scope.locator('not-existing-selector')) if False else scope.locator('button[aria-label]').filter(has_text=regex),
            scope.get_by_role('menuitem', name=regex),
        ])
        if item:
            return item
    return None


def prefer_quality(page):
    """Returns the exact selected UI option, or None. 2K != 2x mathematically."""
    allowed = CONFIG.get('preferred_quality_labels', ['2Ã—', '2x'])
    for value in allowed:
        r = re.compile(r'^\s*' + re.escape(value).replace('\\ ', r'\s*') + r'(?:\s*\b(?:upscale|quality|resolution|image|download)\b.*)?\s*$', re.I)
        option = first_visible([
            page.get_by_role('menuitem', name=r),
            page.get_by_role('option', name=r),
            page.get_by_role('button', name=r),
            page.get_by_text(r),
        ])
        if option:
            return (value, option)
    return None


def wait_for_download(page, emitted: list, seconds):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if emitted:
            return emitted.pop(0)
        page.wait_for_timeout(500)
    return None


def dismiss_flow_overlay(page):
    """Close leftover Angular menus before interacting with a different image.

    Escape is the normal dismissal path; a remaining transparent backdrop can
    be clicked to dismiss it without touching the underlying image card.
    Never remove DOM elements (which could leave the UI in an invalid state).
    """
    backdrop = page.locator('.cdk-overlay-backdrop.cdk-overlay-backdrop-showing')
    if not backdrop.count():
        return
    page.keyboard.press('Escape')
    try:
        backdrop.first.wait_for(state='hidden', timeout=1200)
        return
    except Exception:
        pass
    try:
        if backdrop.count() and backdrop.first.is_visible():
            backdrop.first.click(force=True, timeout=2000)
            backdrop.first.wait_for(state='hidden', timeout=2500)
    except Exception:
        pass
    if backdrop.count() and backdrop.first.is_visible():
        fail('Flow menu overlay remains open and blocks the next image. '
             'Close the popup manually, then rerun; no regeneration is needed.')


def clear_flow_snackbars(page, max_wait_seconds=25):
    """Wait out or dismiss Angular Material snackbars blocking image hotbars.

    Avoid page-wide Escape while a menu is open. Never remove overlay DOM nodes.
    """
    until = time.monotonic() + max_wait_seconds
    while time.monotonic() < until:
        bars = page.locator('.mat-mdc-snack-bar-container, .mdc-snackbar, [matsnackbarlabel]')
        visible = False
        for i in range(min(bars.count(), 12)):
            bar = bars.nth(i)
            try:
                if not bar.is_visible():
                    continue
                visible = True
                # Only dismiss a button belonging to the actual snackbar.
                actions = bar.locator('button').filter(has_text=re.compile(r'Dismiss|Close|Got it|OK', re.I))
                if actions.count() and actions.first.is_visible():
                    actions.first.click(timeout=1800)
                    say('Flow: dismissed blocking snackbar')
                    page.wait_for_timeout(220)
                    break
            except Exception:
                pass
        if not visible:
            return
        page.wait_for_timeout(650)
    # Don't force-click an image under a persistent overlay: wait and report.
    fail('Flow snackbar remained visible after 25 seconds; generation was NOT retried. '
         'Dismiss the notification, then resume the existing batch.')


def open_download_menu(page, image_locator, scopes):
    """Resilient menu opening after concurrent 2K notices. Never regenerate."""
    last_error = None
    for attempt in range(5):
        try:
            clear_flow_snackbars(page)
            dismiss_flow_overlay(page)
            image_locator.hover(timeout=10000)
            clear_flow_snackbars(page)
            download = find_control(page, scopes, r'\bDownload\b')
            if download:
                download.click(timeout=6000)
                return True
            more = find_control(page, scopes, r'^(More|More options|More actions|â‹®|\.\.\.)$')
            if more:
                # A snackbar can appear between hover and click. Retry safely.
                clear_flow_snackbars(page)
                more.click(timeout=6500)
                clear_flow_snackbars(page)
                download = find_control(page, scopes, r'\bDownload\b')
                if download:
                    download.click(timeout=6500)
                    return True
            last_error = 'Download or More options button not visible'
        except Exception as exc:
            last_error = str(exc)[:260]
        say(f'Flow download menu: retry {attempt + 1}/5 after UI obstruction')
        try:
            dismiss_flow_overlay(page)
        except Exception:
            pass
        page.wait_for_timeout(1100)
    fail(f'Flow Download menu inaccessible after snackbar retries: {last_error}. '
         'No new image generations submitted.')

def download_quality_image(page, candidate, emitted, number):
    """Only save if an explicit target-quality label has been selected."""
    img = find_image_locator(page, candidate)
    scopes = image_scopes(img)
    if not open_download_menu(page, img, scopes):
        fail('Could not find the generated image Download control after hovering it.')

    quality = prefer_quality(page)
    if quality is None:
        # Sometimes it is under an Upscale submenu.
        upscale = find_control(page, scopes, r'\bUpscale\b|\bResolution\b|\bQuality\b')
        if upscale:
            upscale.click(timeout=5000)
            quality = prefer_quality(page)
    if quality is None:
        fail('No explicit 2Ã—/2x/2K quality choice found. The agent will NOT silently download 1x.')
    label, option = quality
    say(f'VISUAL {number}: selecting quality menu option: {label}')
    option.click(timeout=10000)
    start = time.monotonic()
    # The selection may itself download, or trigger an upscale task requiring a later download.
    downloaded = wait_for_download(page, emitted, min(25, DOWNLOAD_TIMEOUT))
    if downloaded:
        return downloaded, label

    # Watch for quality processing to finish; do not assume it is instant.
    # A later explicit download must be triggered through the generated image control.
    until = start + QUALITY_TIMEOUT
    while time.monotonic() < until:
        progress = page.get_by_text(re.compile(r'upscal(?:ing|e in progress)|processing image|enhancing', re.I))
        if not progress.count() or not progress.first.is_visible():
            break
        page.wait_for_timeout(1000)
    if time.monotonic() >= until:
        fail('Image upscale still processing past configured timeout.')

    img = find_image_locator(page, candidate)
    scopes = image_scopes(img)
    if not open_download_menu(page, img, scopes):
        fail('Quality was chosen, but the finished image Download button is unavailable.')
    downloaded = wait_for_download(page, emitted, max(1, DOWNLOAD_TIMEOUT - 25))
    if not downloaded:
        # Download might present quality menu again; click EXACTLY same quality.
        another = prefer_quality(page)
        if another:
            value, button = another
            button.click(timeout=10000)
            downloaded = wait_for_download(page, emitted, DOWNLOAD_TIMEOUT)
            label = value
    if not downloaded:
        fail('Quality option selected but browser did not report a completed image download.')
    return downloaded, label


def save_verified(download, number, label):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    suggested_suffix = Path(download.suggested_filename).suffix.lower()
    suffix = suggested_suffix if suggested_suffix in {'.png', '.jpg', '.jpeg', '.webp'} else '.png'
    final = OUTPUT / f'visual_{number:03d}{suffix}'
    if final.exists():
        fail(f'Target file already exists: {final}. Refusing to overwrite.')
    temp = OUTPUT / f'.visual_{number:03d}.partial{suffix}'
    try:
        download.save_as(str(temp))
        if download.failure():
            fail(f'Browser download failure: {download.failure()}')
        with Image.open(temp) as im:
            im.verify()
        with Image.open(temp) as im:
            width, height = im.size
        if max(width, height) < int(CONFIG.get('minimum_download_width_or_height', 1200)):
            fail(f'Download dimensions are {width}x{height}. Not large enough to validate high-res output.')
        if temp.stat().st_size < 10_000:
            fail('Downloaded image file is suspiciously small.')
        temp.replace(final)
        return str(final), width, height
    finally:
        if temp.exists():
            # Keep failed files outside the output directory so they cannot be mistaken for deliverables.
            quarantine = WORK / 'quarantine'
            quarantine.mkdir(parents=True, exist_ok=True)
            temp.replace(quarantine / (temp.name + f'.{int(time.time())}'))


def main():
    parser = argparse.ArgumentParser(description='ChatGPT image prompts -> Google Flow 2x/2K image downloader')
    parser.add_argument('mode', choices=['inspect', 'scan', 'test', 'run'], nargs='?', default='test')
    parser.add_argument('--max', type=int, default=0, help='Maximum number of new images (run mode); test always does one')
    args = parser.parse_args()

    WORK.mkdir(parents=True, exist_ok=True)
    journal = Journal(WORK / 'state.json')
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.connect_over_cdp(CONFIG['cdp_url'], timeout=20000)
        except Exception as exc:
            fail(f'Cannot attach to Chrome on {CONFIG["cdp_url"]}. Run .\\launch-chrome.ps1 first. ({exc})')
        if not browser.contexts:
            fail('Chrome has no attachable browser context.')
        context = browser.contexts[0]
        try:
            if args.mode == 'inspect':
                say('INSPECT ONLY â€” will not generate or spend Flow credits.')
                for page in context.pages:
                    say(f'Open tab: {page.url}')
                flow = find_flow_page(context)
                capture_diagnostics(flow, 'inspect_flow')
                return

            prompts = find_chatgpt_prompts(context)
            if args.mode == 'scan':
                preview = [{'number': p.number, 'characters': len(p.prompt), 'preview': p.prompt[:110], 'sent_to_flow': flow_prompt_text(p.prompt)} for p in prompts]
                (WORK / 'prompt_preview.json').write_text(json.dumps(preview, indent=2, ensure_ascii=False), encoding='utf-8')
                say(f'SCAN ONLY â€” wrote prompt_preview.json in {WORK}. No images generated.')
                return

            flow = find_flow_page(context)
            flow.bring_to_front()
            output_count = 0
            max_items = 1 if args.mode == 'test' else (args.max if args.max > 0 else len(prompts))
            for visual in prompts:
                status = journal.item(visual.number)
                if status and status['hash'] != visual.hash:
                    fail(f'VISUAL {visual.number} prompt differs from state. No automatic overwrite or remake.')
                if status and status['status'] == 'completed':
                    file = Path(status['file'])
                    if file.exists():
                        say(f'VISUAL {visual.number}: already completed; skipping {file}')
                        continue
                    fail(f'VISUAL {visual.number}: journal says complete but file is missing: {file}')
                if status and status['status'] not in ('pending',):
                    fail(f'VISUAL {visual.number}: journal status={status["status"]}. '
                         'It may already exist in Flow. Review the Flow project and state.json manually; '
                         'no automatic regeneration to avoid wasting credits.')
                if output_count >= max_items:
                    break

                say(f'VISUAL {visual.number}: preparing image generation ({len(visual.prompt)} chars)')
                try:
                    generate = choose_image_mode(flow)
                    editor = find_prompt_input(flow)
                    editor.fill(flow_prompt_text(visual.prompt), timeout=12000)
                    if not generate.is_visible() or not generate.is_enabled():
                        fail('Start generation is still disabled after entering the prompt. Check Flow for validation errors or account limits.')
                    before = image_fingerprints(flow)
                    # Mark UNCERTAIN before clicking; on crash do not blindly resubmit.
                    journal.put(visual.number, visual.hash, 'submitting', source='ChatGPT')
                    generate.click(timeout=12000)
                    journal.put(visual.number, visual.hash, 'submitted')
                    say(f'VISUAL {visual.number}: sent to Flow; waiting for a NEW rendered image')
                    new_image = new_image_after_submit(flow, before, IMAGE_TIMEOUT)
                    say(f'VISUAL {visual.number}: generated preview detected ({new_image["width"]}x{new_image["height"]})')
                    journal.put(visual.number, visual.hash, 'image_ready', preview_dimensions=[new_image['width'], new_image['height']])
                    emitted = []
                    cb = lambda dl: emitted.append(dl)
                    flow.on('download', cb)
                    try:
                        download, quality = download_quality_image(flow, new_image, emitted, visual.number)
                        path, width, height = save_verified(download, visual.number, quality)
                    finally:
                        flow.remove_listener('download', cb)
                    journal.put(visual.number, visual.hash, 'completed', file=path, width=width, height=height, quality_ui_label=quality)
                    say(f'VISUAL {visual.number}: SUCCESS â€” {path} ({width}x{height}, selected {quality})')
                    output_count += 1
                except Exception as exc:
                    capture_diagnostics(flow, f'error_visual_{visual.number}')
                    old = journal.item(visual.number)
                    if old:
                        journal.put(visual.number, visual.hash, 'needs_review', reason=str(exc))
                    else:
                        journal.event(visual.number, 'failed_before_submission', reason=str(exc))
                    raise
            say(f'Done. New images downloaded this run: {output_count}; folder: {OUTPUT}')
        finally:
            # Disconnects from existing Chrome; does not close the user's browser.
            browser.close()


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, PlaywrightTimeout) as error:
        print(f'\nSTOPPED: {error}', file=sys.stderr)
        sys.exit(1)

