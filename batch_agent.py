"""Submit five Flow image requests before waiting, then match and download safely.

True overlapping generation is attempted. Flow may rate-limit or serialize requests.
Image assignment requires an unambiguous prompt/image association in Flow's DOM;
we never attribute gallery images by arrival order.
"""
import argparse
import os
import re
import sys
import time
from pathlib import Path

# Prevent Windows CP1252/charmap failures for text that contains symbols.
os.environ.setdefault('PYTHONUTF8', '1')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8:replace')
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, 'reconfigure'):
        try: _stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception: pass

def journal_safe(value):
    """Keep journal metadata ASCII-only for older journal.py Windows writers."""
    return str(value).encode('ascii','backslashreplace').decode('ascii')

from playwright.sync_api import sync_playwright
from agent import (CONFIG, WORK, IMAGE_TIMEOUT, Journal, find_chatgpt_prompts,
    find_flow_page, find_prompt_input, choose_image_mode, image_fingerprints,
    download_quality_image, save_verified, flow_prompt_text,
    capture_diagnostics, say, fail, find_image_locator, image_scopes,
    dismiss_flow_overlay, open_download_menu, find_control, prefer_quality,
    DOWNLOAD_TIMEOUT, QUALITY_TIMEOUT, OUTPUT)


def already_done(visual, journal):
    status = journal.item(visual.number)
    if status and status['hash'] != visual.hash:
        fail(f'VISUAL {visual.number}: different prompt recorded. Resolve manually.')
    if status and status['status'] == 'completed':
        file = Path(status['file']) if status.get('file') else None
        if file and file.is_file() and file.stat().st_size > 10_000:
            say(f'VISUAL {visual.number}: already downloaded — skipping')
            return True
        say(f'VISUAL {visual.number}: previous file is missing; recovering its existing Flow image')
    return False


def unique_prompt_phrase(prompt, all_prompts):
    """Find a distinctive passage, not merely a shared opening of prompts."""
    normal = lambda value: re.sub(r'\s+', ' ', value).strip()
    original = normal(prompt)
    others = [normal(other) for other in all_prompts if other != prompt]
    # Check progressively later portions: visuals in a series often share the
    # same header and camera/style instructions.
    for size in (130, 100, 75, 50):
        for start in range(0, max(1, len(original) - size + 1), 25):
            phrase = original[start:start + size]
            if len(phrase) < 45:
                continue
            if not any(phrase in other for other in others):
                return phrase
        if len(original) >= size:
            phrase = original[-size:]
            if not any(phrase in other for other in others):
                return phrase
    fail('Prompts are indistinguishable by visible text; manual review required.')


def prompt_image_matches(page, prompt, all_prompts):
    """Return visible image fingerprints ONLY for cards containing this exact prompt.

    Constrain the matching container to one generated image, and require a long
    unique passage of the original prompt to appear as visible or accessible
    text. This deliberately fails closed if Flow hides the prompt in a menu.
    """
    phrase = unique_prompt_phrase(prompt, all_prompts)
    return page.evaluate('''(phrase) => {
      const norm = s => (s || '').replace(/\\s+/g, ' ').trim();
      const needle = norm(phrase);
      const output = [];
      const seen = new Set();
      for (const im of document.images) {
        const rect = im.getBoundingClientRect();
        if (!im.complete || im.naturalWidth < 350 || im.naturalHeight < 350 ||
            rect.width < 100 || rect.height < 80) continue;
        const key = im.currentSrc || im.src;
        if (!key || seen.has(key)) continue;
        for (let p = im.parentElement, depth = 0; p && depth < 12; p = p.parentElement, depth++) {
          // Avoid attributing an image to a prompt mentioned elsewhere on the page.
          const imgs = Array.from(p.querySelectorAll('img')).filter(x => x.naturalWidth >= 350 && x.naturalHeight >= 350);
          if (imgs.length > 1) break;
          const raw = norm(p.innerText || p.textContent || '');
          const aria = norm(p.getAttribute('aria-label') || '');
          const title = norm(p.getAttribute('title') || '');
          if ((raw.includes(needle) || aria.includes(needle) || title.includes(needle)) && needle.length >= 45) {
            output.push({key, width: im.naturalWidth, height: im.naturalHeight});
            seen.add(key);
            break;
          }
        }
      }
      return output;
    }''', phrase)


def submit(page, visual, journal):
    say(f'VISUAL {visual.number}: submitting (no preview wait)')
    try:
        generate = choose_image_mode(page)
        find_prompt_input(page).fill(flow_prompt_text(visual.prompt), timeout=12000)
        if not generate.is_visible() or not generate.is_enabled():
            fail('Flow generation button unavailable. Queueing may be blocked.')
        journal.put(visual.number, visual.hash, 'submitting', source='ChatGPT',
                    prompt_for_matching=journal_safe(visual.prompt[:350]))
        page.keyboard.press('Escape')
        overlay = page.locator('.cdk-overlay-backdrop.cdk-overlay-backdrop-showing')
        overlay.first.wait_for(state='hidden', timeout=5000) if overlay.count() else None
        generate.click(timeout=12000)
        journal.put(visual.number, visual.hash, 'submitted')
        say(f'VISUAL {visual.number}: submitted; proceeding to next prompt')
    except Exception as exc:
        capture_diagnostics(page, f'concurrent_submit_visual_{visual.number}')
        # On uncertain click outcome, never silently resubmit.
        if journal.item(visual.number):
            journal.put(visual.number, visual.hash, 'needs_review', reason=journal_safe(exc))
        raise


def normalized_prompt(value):
    return re.sub(r'\s+', ' ', value or '').strip().casefold()


def editor_text(editor):
    return editor.evaluate("el => ('value' in el) ? (el.value || '') : (el.innerText || el.textContent || '')")


def clear_reused_prompt(page):
    """Clear Flow's reused prompt without submitting any generation.

    Some versions keep a reactive composer chip after Playwright .fill('').
    Prefer the editor's own clear control, then use normal keyboard editing.
    Never press Enter, Generate, or an unscoped page-level close button.
    """
    editor = find_prompt_input(page)

    def empty():
        try:
            return not normalized_prompt(editor_text(find_prompt_input(page)))
        except Exception:
            return False

    if empty():
        dismiss_flow_overlay(page)
        return

    # First try the same editor input API used for normal prompt submission.
    try:
        editor.fill('', timeout=4000)
        page.wait_for_timeout(250)
        if empty():
            dismiss_flow_overlay(page)
            return
    except Exception:
        pass

    # Angular/ProseMirror-style editors can require actual key events.
    try:
        editor = find_prompt_input(page)
        editor.click(timeout=3000)
        editor.press('ControlOrMeta+A', timeout=3000)
        editor.press('Backspace', timeout=3000)
        page.wait_for_timeout(300)
        if empty():
            dismiss_flow_overlay(page)
            return
    except Exception:
        pass

    # Flow also displays an X next to a reused prompt. Only target controls
    # close to the prompt editor; never click an arbitrary X in the gallery.
    try:
        editor = find_prompt_input(page)
        scopes = [editor.locator('xpath=..'),
                  editor.locator('xpath=../..'),
                  editor.locator('xpath=../../..')]
        selectors = [
            'button[aria-label*="clear" i]',
            'button[aria-label*="remove" i]',
            'button[aria-label*="close prompt" i]',
            'button[title*="clear" i]',
            'button[title*="remove" i]',
        ]
        for scope in scopes:
            for selector in selectors:
                buttons = scope.locator(selector)
                if buttons.count() != 1:
                    continue
                button = buttons.first
                if button.is_visible() and button.is_enabled():
                    button.click(timeout=2500)
                    page.wait_for_timeout(250)
                    if empty():
                        dismiss_flow_overlay(page)
                        return
    except Exception:
        pass

    # Fail closed: do not run another job with someone else's reused prompt.
    fail('Could not clear reused prompt with editor fill, keyboard deletion, '
         'or a scoped clear button. Close the reused prompt manually and '
         'rerun; no automatic generation or journal reset is needed.')


class StaleFlowCard(RuntimeError):
    """The candidate from a prior DOM scan is no longer a unique live image.

    This is expected when Flow virtualizes/recycles cards while generating.
    Never click an alternative image; simply rescan the gallery.
    """


def open_reuse_prompt(page, candidate):
    """Use a stable media ID and re-resolve the image after gallery reflows.

    Never click another image when Flow has re-ordered its grid. A transient
    angular animation may detach/recreate the image or its More button.
    """
    from urllib.parse import urlparse

    key = candidate['key']
    match_id = re.search(r'/image/([0-9a-fA-F-]{36})', key)
    media_id = match_id.group(1) if match_id else None

    def current_image():
        if media_id:
            # Flow renders a stable media identifier in the img element; using
            # this prevents nth(2) from silently becoming a different image.
            images = page.locator(f'img[data-media-id="{media_id}"]')
            if images.count() == 1:
                return images.first
            # Some versions omit data-media-id; match the immutable image ID.
            images = page.locator(f'img[src*="/image/{media_id}"]')
            if images.count() == 1:
                return images.first
            if images.count() > 1:
                raise StaleFlowCard(f'Flow media ID {media_id} appears multiple times')
        images = page.locator('img')
        matches = []
        for j in range(images.count()):
            item = images.nth(j)
            try:
                if item.evaluate('(el) => el.currentSrc || el.src') == key:
                    matches.append(item)
            except Exception:
                continue
        if len(matches) != 1:
            raise StaleFlowCard('Target image missing or duplicated in Flow gallery')
        return matches[0]

    last_error = None
    for attempt in range(5):
        try:
            dismiss_flow_overlay(page)
            image = current_image()
            image.scroll_into_view_if_needed(timeout=5000)
            # Hovering causes the per-card More button to appear.
            image.hover(force=True, timeout=4000)
            scopes = image_scopes(current_image())
            for scope in scopes:
                buttons = scope.locator('button[aria-label*="More" i], button[title*="More" i], button[aria-label*="options" i], button[aria-haspopup="menu"], button:has-text("more_vert")')
                for j in range(min(buttons.count(), 6)):
                    button = buttons.nth(j)
                    if not button.is_visible():
                        continue
                    # Flow's cards animate and Playwright's stability check can
                    # fail even for the correct button. force bypasses stability,
                    # but NOT the stable card/media-ID selection above.
                    button.click(force=True, timeout=4000)
                    reuse = page.get_by_text('Reuse prompt', exact=True)
                    if reuse.count() and reuse.first.is_visible():
                        reuse.first.click(timeout=4000)
                        return
                    dismiss_flow_overlay(page)
                    image = current_image()
                    image.hover(force=True, timeout=4000)
            # No coordinate clicks: they can land on the profile/navigation UI.
            raise StaleFlowCard('Exact image-card More control unavailable; rescan safely')
        except StaleFlowCard:
            # DOM became stale; don't keep retrying a card that is gone.
            raise
        except Exception as exc:
            last_error = exc
            try:
                page.keyboard.press('Escape')
                dismiss_flow_overlay(page)
                page.wait_for_timeout(650)
            except Exception:
                pass
    fail(f'Could not open Reuse prompt for media {media_id or key[:80]} after 5 retries: '
         f'{last_error}. No regeneration occurred.')


def identify_by_reuse(page, candidate, visuals):
    """Read original prompt from the actual card using Flow's Reuse prompt UI."""
    editor = find_prompt_input(page)
    existing = normalized_prompt(editor_text(editor))
    if existing:
        # Reuse leaves full prompt text in the composer. Clear ONLY one of our
        # known generated prompts; never overwrite a user's unrelated draft.
        owned_prompts = {normalized_prompt(flow_prompt_text(v.prompt)) for v in visuals}
        if existing not in owned_prompts:
            fail('Flow composer contains an unrelated draft; refusing to overwrite it.')
        clear_reused_prompt(page)
    try:
        open_reuse_prompt(page, candidate)
        # The editor is populated on the same page; do not submit anything.
        editor = find_prompt_input(page)
        editor.wait_for(state='visible', timeout=6000)
        text = editor_text(editor)
        if not normalized_prompt(text):
            fail('Reuse prompt did not populate Flow editor; no image assigned.')
        restored = normalized_prompt(text)
        matches = [v for v in visuals if restored == normalized_prompt(flow_prompt_text(v.prompt))]
        if len(matches) > 1:
            fail(f'Reused prompt matches multiple visuals {[v.number for v in matches]}; stop.')
        return matches[0] if matches else None
    finally:
        # Always clear the composer, even if matching failed.
        clear_reused_prompt(page)


def _gallery_scroller_action(page, operation):
    """Move ONLY the scrollable ancestor of a real Flow-generated image.

    Never guess the gallery by document height: doing so moved the whole page
    underneath the profile button. If the images have no own scroll container,
    leave the current viewport alone instead of scrolling unrelated UI.
    """
    return page.evaluate("""(op) => {
      const images = [...document.images].filter(im =>
        im.naturalWidth >= 350 && im.naturalHeight >= 350 &&
        (im.currentSrc || im.src || '').includes('/image/'));
      for (const image of images) {
        for (let el = image.parentElement; el && el !== document.body;
             el = el.parentElement) {
          const cs = getComputedStyle(el);
          if (!/(auto|scroll)/.test(cs.overflowY) ||
              el.scrollHeight <= el.clientHeight + 80 || el.clientHeight < 180)
            continue;
          const before = el.scrollTop;
          if (op === 'top') el.scrollTop = 0;
          if (op === 'bottom') el.scrollTop = el.scrollHeight;
          if (op === 'up') el.scrollTop -= Math.max(150, el.clientHeight * .75);
          if (op === 'down') el.scrollTop += Math.max(150, el.clientHeight * .75);
          return {found:true, before, after:el.scrollTop,
                  height:el.scrollHeight, viewport:el.clientHeight};
        }
      }
      return {found:false, before:0, after:0, height:0, viewport:0};
    }""", operation)


def gallery_bottom_first(page):
    """Scan ONLY an image-containing Flow scroll panel, bottom-to-top.

    Maximum 12 short scroll steps; if cards aren't in a dedicated scrolling
    gallery, inspect the currently visible cards only, never the whole page.
    """
    dims = _gallery_scroller_action(page, 'bottom')
    page.wait_for_timeout(300)
    found = []
    seen = set()
    steps = min(12, max(1, int(dims['height'] / max(1, dims['viewport'] * .75)) + 1)) if dims['found'] else 1
    for index in range(steps):
        current = image_fingerprints(page)
        for card in reversed(current):
            if card['key'] not in seen:
                seen.add(card['key'])
                found.append(card)
        if index + 1 == steps:
            break
        move = _gallery_scroller_action(page, 'up')
        if not move['found'] or move['after'] >= move['before']:
            break
        page.wait_for_timeout(220)
    return found


def gallery_top_first(page):
    """Return ONLY on-screen gallery cards from top to bottom.

    Never scroll downward to enumerate the entire gallery while trashing.
    Deleting the topmost card allows lower cards to move into view naturally.
    """
    # Only move the image gallery itself, not the profile/header page.
    _gallery_scroller_action(page, 'top')
    page.wait_for_timeout(350)
    ranked = []
    for card in image_fingerprints(page):
        key = card['key']
        try:
            image = find_image_locator(page, card)
            rect = image.evaluate("""el => {
                const r = el.getBoundingClientRect();
                const w = window.innerWidth, h = window.innerHeight;
                return {top:r.top, left:r.left, right:r.right, bottom:r.bottom,
                        width:r.width, height:r.height, vw:w, vh:h};
            }""")
            if (rect['width'] < 100 or rect['height'] < 80 or
                rect['bottom'] <= 0 or rect['top'] >= rect['vh'] or
                rect['right'] <= 0 or rect['left'] >= rect['vw']):
                continue
            ranked.append((rect['top'], rect['left'], card))
        except Exception:
            continue
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [card for _, _, card in ranked]


def _exact_image_three_dot_once(page, image):
    """Find ONLY the three-dot menu on the target image card, never page-wide controls.

    Flow has used Material icon ligatures and SVG circle icons with no accessible
    name. These are specifically checked rather than clicking random buttons.
    """
    image.hover(force=True, timeout=5000)
    for scope in image_scopes(image):
        try:
            if scope.locator('img').count() != 1:
                continue
            buttons = scope.locator('button, [role="button"]')
            matches = []
            for i in range(min(buttons.count(), 24)):
                button = buttons.nth(i)
                if not button.is_visible() or not button.is_enabled():
                    continue
                exact_three_dot = button.evaluate(r"""(el) => {
                    const meta = [el.getAttribute('aria-label'), el.getAttribute('title'),
                      el.getAttribute('data-tooltip'), el.innerText, el.textContent]
                      .filter(Boolean).join(' ').trim();
                    if (/\b(more_vert|more_horiz|more options|more actions|image options)\b/i.test(meta)) return true;
                    if (/^\s*(⋮|⋯|\.\.\.)\s*$/.test(meta)) return true;
                    const icons = [...el.querySelectorAll('svg')];
                    return icons.some(svg => {
                      const label = [svg.getAttribute('aria-label'), svg.getAttribute('data-testid')].filter(Boolean).join(' ');
                      if (/more|dots|ellipsis/i.test(label)) return true;
                      const cs = [...svg.querySelectorAll('circle')];
                      if (cs.length !== 3) return false;
                      const pts = cs.map(c => [Number(c.getAttribute('cx')), Number(c.getAttribute('cy'))]);
                      return pts.every(p => p.every(Number.isFinite)) &&
                        (pts.every(p => p[0] === pts[0][0]) || pts.every(p => p[1] === pts[0][1]));
                    });
                }""")
                if exact_three_dot:
                    matches.append(button)
            if len(matches) == 1:
                return matches[0]
        except Exception:
            continue
    return None



def _exact_image_three_dot(page, image):
    """Retry the exact same Flow image's menu after tab focus / gallery reflow."""
    if not re.search(r'https?://(?:labs\.google|flow\.google)\.', page.url, re.I):
        fail(f'Expected Google Flow image page, got {page.url!r}; no controls clicked.')
    try:
        image_key = image.evaluate('(el) => el.currentSrc || el.src')
    except Exception as exc:
        fail(f'Cannot identify target Flow image before menu retry: {exc}')
    if not image_key:
        fail('Target Flow image has no stable URL; no menu clicked.')
    for attempt in range(1, 7):
        # Prefetch can foreground ChatGPT. Always re-activate the original Flow page.
        page.bring_to_front()
        try:
            candidate = find_image_locator(page, {'key': image_key})
            key_now = candidate.evaluate('(el) => el.currentSrc || el.src')
            if key_now != image_key:
                raise StaleFlowCard('The original media key changed')
            result = _exact_image_three_dot_once(page, candidate)
            if result is not None:
                say(f'Flow: verified original card three-dot menu (attempt {attempt}/6).') if attempt > 1 else None
                return result
        except Exception as exc:
            say(f'Flow: exact image menu not ready ({attempt}/6): {str(exc)[:90]}')
        if attempt < 6:
            page.wait_for_timeout(1200)
    capture_diagnostics(page, 'three_dot_control_missing_after_retries')
    fail('Three-dot image menu not found on the SAME Flow image after 6 retries; no other card clicked.')


def _open_exact_image_trash_menu(page, image):
    """Click only the corresponding three-dot button, then require exact Trash item."""
    button = _exact_image_three_dot(page, image)
    button.click(timeout=5000)
    # Flow labels this action 'Move to bin' (not always 'Move to trash').
    # Only accept an exact, visible action within the open menu; never click
    # image cards or page-level Trash navigation as a fallback.
    label = re.compile(r'^\s*Move to (?:bin|trash)\s*$', re.I)
    candidates = [
        page.get_by_role('menuitem', name=label),
        page.get_by_text(label),
    ]
    for locator in candidates:
        matches = []
        for i in range(min(locator.count(), 10)):
            item = locator.nth(i)
            if item.is_visible() and item.is_enabled():
                matches.append(item)
        if len(matches) == 1:
            say('Flow: target image menu shows Move to bin/trash; selecting exact action.')
            return matches[0]
    capture_diagnostics(page, 'three_dot_menu_no_bin')
    fail('Three-dot menu opened but no unambiguous Move to bin/trash action was visible.')


def clear_flow_gallery(page):
    """Clear active Flow project's gallery, re-resolving a card after every removal.

    Uses the exact "Move to trash" action visible in Flow's card More menu.
    A recycled/vanished gallery image is a reason to rescan, NOT abort.
    """
    if 'labs.google' not in page.url and 'flow.google' not in page.url:
        fail(f'Refusing trash outside Google Flow: {page.url}')
    say('CLEAR FLOW PROJECT: deleting TOP image first (image menu > Move to bin).')
    deleted = 0
    repeated = {}
    empty_scans = 0
    while deleted < 1000:
        cards = gallery_top_first(page)
        if not cards:
            empty_scans += 1
            if empty_scans >= 3:
                say(f'CLEAR FLOW PROJECT: gallery empty ({deleted} items moved to trash).')
                return
            page.wait_for_timeout(1100)
            continue
        empty_scans = 0
        # Fresh list each iteration: Flow removes/reorders cards after each trash.
        # Prefer a card without previous transient lookup failures.
        candidate = next((c for c in cards if repeated.get(c['key'], 0) < 5), None)
        if candidate is None:
            capture_diagnostics(page, 'flow_trash_cards_unreachable')
            fail('Flow gallery cards remain but cannot be targeted after rescan; stopped safely.')
        key = candidate['key']
        try:
            # Exact media key is rechecked on every attempt; never use a stale index.
            img = find_image_locator(page, candidate)
            # Never auto-scroll down while trashing; only target an on-screen card.
            bounds = img.bounding_box(timeout=3000)
            # CDP-connected Chrome contexts frequently return viewport_size=None.
            # Query the real visible height from the page instead.
            viewport_height = page.evaluate('() => window.innerHeight')
            if (not bounds or bounds['y'] + bounds['height'] <= 0 or
                bounds['y'] >= viewport_height):
                raise RuntimeError('Top gallery card moved off-screen; rescan from top')
            # Click ONLY the target card's dedicated three-dot More menu.
            action = _open_exact_image_trash_menu(page, img)
            action.first.click(timeout=4000)
            dialog = page.get_by_role('dialog')
            if dialog.count() and dialog.first.is_visible():
                confirm = dialog.first.get_by_role('button', name=re.compile(r'^(Move to (?:bin|trash)|Bin|Trash|Delete)$', re.I))
                if confirm.count() != 1:
                    fail('Trash confirmation dialog is ambiguous; stopping safely.')
                confirm.click(timeout=4000)
            page.wait_for_timeout(900)
            # A disappearing original card is normal success after trash.
            if any(c['key'] == key for c in image_fingerprints(page)):
                # UI may be updating; do not count as removed until a rescan.
                page.wait_for_timeout(1400)
                if any(c['key'] == key for c in gallery_top_first(page)):
                    raise RuntimeError('Card still visible after Move to bin')
            deleted += 1
            repeated.pop(key, None)
            say(f'Flow trash: {deleted} image(s) removed')
        except Exception as exc:
            repeated[key] = repeated.get(key, 0) + 1
            say(f'Flow trash: card changed or menu unavailable; rescanning ({repeated[key]}/5): {str(exc)[:95]}')
            try:
                page.keyboard.press('Escape')
            except Exception:
                pass
            page.wait_for_timeout(900)
            if repeated[key] >= 5:
                capture_diagnostics(page, 'flow_trash_failed')
                fail('Flow trash could not access one card after 5 rescans; stopping safely. ' + str(exc)[:130])
    fail('Flow trash safety cap of 1000 cards reached.')


def render_matches(page, visuals, journal, baseline, all_visuals=None):
    """Scan safely and stop after five scans without any NEW verified match.

    Return only missing visuals; caller performs bounded recovery/regeneration.
    """
    expected = all_visuals or visuals
    remaining = {v.number: v for v in visuals}
    scanned = set()
    retry = {}
    no_progress = 0
    max_no_progress = 5
    deadline = time.monotonic() + IMAGE_TIMEOUT + 60 * max(0, len(visuals) - 1)
    while remaining and no_progress < max_no_progress and time.monotonic() < deadline:
        before = len(remaining)
        cards = gallery_bottom_first(page)
        for candidate in cards:
            key = candidate['key']
            if key in scanned or retry.get(key, 0) > time.monotonic():
                continue
            try:
                match = identify_by_reuse(page, candidate, expected)
            except Exception as exc:
                say(f'Flow card temporarily unavailable: {str(exc)[:95]}; will rescan')
                retry[key] = time.monotonic() + 12
                continue
            scanned.add(key)
            if match is None or match.number not in remaining:
                continue
            journal.put(match.number, match.hash, 'image_ready', image_key=key,
                        preview_dimensions=[candidate['width'], candidate['height']])
            del remaining[match.number]
            say(f'VISUAL {match.number}: verified by full original Reuse prompt')
        if not remaining:
            break
        if len(remaining) < before:
            no_progress = 0
        else:
            no_progress += 1
        missing_numbers = ', '.join(str(n) for n in sorted(remaining))
        say(f'Unverified visuals: {missing_numbers}; no-progress scan '
            f'{no_progress}/{max_no_progress}')
        if no_progress < max_no_progress:
            page.wait_for_timeout(5000)
    if remaining:
        missing_numbers = ', '.join(str(n) for n in sorted(remaining))
        say(f'Preview scan limit reached. Handing VISUAL {missing_numbers} '
            'to existing recovery/regeneration; no more repeated gallery scrolling.')
    return list(remaining.values())


def start_2k_in_background(page, candidate, number):
    """Click 2K, dismiss the *notification*, then immediately return.

    Never wait for the upscale inside this function. One Flow tab only.
    """
    img = find_image_locator(page, candidate)
    scopes = image_scopes(img)
    if not open_download_menu(page, img, scopes):
        fail(f'VISUAL {number}: Download menu not available')
    quality = prefer_quality(page)
    if quality is None:
        extra = find_control(page, scopes, r'\bUpscale\b|\bResolution\b|\bQuality\b')
        if extra:
            extra.click(timeout=5000)
            quality = prefer_quality(page)
    if quality is None:
        fail(f'VISUAL {number}: explicit 2K option not found')
    label, option = quality
    say(f'VISUAL {number}: starting 2K upscale/download: {label}')
    option.click(timeout=10000)
    # The notification reads: "Upscaling your image, download will start automatically."
    # Dismiss *only* that notification; it does not cancel the background task.
    try:
        notification = page.get_by_text('Upscaling your image, download will start automatically.', exact=False)
        if notification.count():
            # Scope to a parent containing the visible Dismiss button.
            for depth in ('..', '../..', '../../..', '../../../..'):
                pane = notification.first.locator('xpath=' + depth)
                btn = pane.get_by_role('button', name='Dismiss', exact=True)
                if btn.count() == 1 and btn.first.is_visible():
                    btn.first.click(timeout=1500)
                    say(f'VISUAL {number}: upscale running in background; dismissed notice')
                    break
    except Exception:
        # Notification may vanish before we inspect it; never block other upscales.
        pass
    dismiss_flow_overlay(page)
    return label


def match_download_identity(download, jobs):
    """Return the only requested visual identified by media ID in URL/filename.

    Background upscales may FINISH OUT OF ORDER. No guessing by event order.
    """
    data = ((download.url or '') + ' ' + (download.suggested_filename or '')).lower()
    hits = [n for n, job in jobs.items()
            if job['media_id'] and job['media_id'] in data]
    if len(hits) == 1:
        return hits[0]
    # A single outstanding download is unambiguous even with an opaque URL.
    if len(jobs) == 1:
        return next(iter(jobs))
    return None


# SEQUENTIAL_CHECKED_PATCH_V1

def _unique_gallery_cards(page):
    """Return unique loaded Flow image cards; no visual/content matching."""
    found = {}
    for card in image_fingerprints(page):
        key = card.get('key')
        if key and key not in found:
            found[key] = card
    return list(found.values())


def _wait_for_batch_count(page, expected):
    """Wait until exactly the submitted number of image cards is stable."""
    # Keep the user's established 90-second initial wait, then poll safely.
    say(f'Waiting 90 seconds for {expected} Flow images to finish.')
    page.wait_for_timeout(90000)
    timeout_seconds = max(IMAGE_TIMEOUT * 3, expected * 75, 600)
    deadline = time.monotonic() + timeout_seconds
    last_count = None
    stable = 0
    last_reported = None
    while time.monotonic() < deadline:
        cards = _unique_gallery_cards(page)
        count = len(cards)
        if count > expected:
            capture_diagnostics(page, 'top_first_image_count_exceeded')
            fail(f'Expected {expected} image cards but Flow shows {count}. No downloads or deletions were started; check that image count is set to x1.')
        if count == expected:
            stable = stable + 1 if count == last_count else 1
            if stable >= 3:
                say(f'Flow batch ready: exactly {expected} stable image cards. Top-first mode enabled; no prompt/image matching.')
                return
        else:
            stable = 0
            if count != last_reported:
                say(f'Flow images ready: {count}/{expected}; waiting for the remaining images (no regeneration).')
                last_reported = count
        last_count = count
        page.wait_for_timeout(5000)
    capture_diagnostics(page, 'top_first_image_count_timeout')
    fail(f'Flow showed only {len(_unique_gallery_cards(page))}/{expected} expected image cards before timeout. Nothing was downloaded or deleted; gallery remains available for recovery.')


def _trash_downloaded_top_card(page, candidate, expected_after):
    """Move only the just-downloaded top card to Flow trash, then verify the count."""
    key = candidate['key']
    if 'labs.google' not in page.url and 'flow.google' not in page.url:
        fail(f'Refusing to trash outside Google Flow: {page.url}')

    # Re-resolve the same top card immediately before opening its menu.
    top = gallery_top_first(page)
    if not top or top[0].get('key') != key:
        capture_diagnostics(page, 'top_first_card_order_changed')
        fail('Gallery order changed before deletion. The downloaded file is saved, but no other image was deleted.')

    image = find_image_locator(page, candidate)
    image.scroll_into_view_if_needed(timeout=8000)
    # Exact target-card three-dot control only, not generic More buttons.
    action = _open_exact_image_trash_menu(page, image)
    action.first.click(timeout=5000)

    dialog = page.get_by_role('dialog')
    if dialog.count() and dialog.first.is_visible():
        confirm = dialog.first.get_by_role('button', name=re.compile(r'^(Move to (?:bin|trash)|Bin|Trash|Delete)$', re.I))
        if confirm.count() != 1:
            capture_diagnostics(page, 'top_first_delete_confirmation_ambiguous')
            fail('Trash confirmation was ambiguous. Download is saved; no confirmation clicked.')
        confirm.click(timeout=5000)

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        cards = _unique_gallery_cards(page)
        keys = {item['key'] for item in cards}
        if key not in keys and len(cards) == expected_after:
            say(f'Flow top card deleted; {expected_after} image(s) remain. Next top image will be processed.')
            return
        page.wait_for_timeout(700)
    capture_diagnostics(page, 'top_first_delete_not_confirmed')
    fail(f'Download is saved, but Flow did not settle to the expected {expected_after} remaining cards. Stopped before touching another image.')



def _wait_for_one_checked_card(page, number):
    """One submitted prompt, one loaded image. Stop on ambiguity."""
    deadline = time.monotonic() + max(IMAGE_TIMEOUT * 3, 600)
    previous = None
    stable = 0
    while time.monotonic() < deadline:
        cards = _unique_gallery_cards(page)
        if len(cards) > 1:
            capture_diagnostics(page, f'seq_extra_cards_{number}')
            fail(f'VISUAL {number}: Flow has {len(cards)} image cards (expected exactly one). Nothing downloaded or deleted.')
        if len(cards) == 1:
            card = cards[0]
            key = card.get('key')
            stable = stable + 1 if key == previous else 1
            if stable >= 3:
                top = gallery_top_first(page)
                if len(top) != 1 or top[0].get('key') != key:
                    capture_diagnostics(page, f'seq_top_mismatch_{number}')
                    fail(f'VISUAL {number}: gallery/card selection changed before download; stopped.')
                say(f'VISUAL {number}: PRE-DOWNLOAD CHECK PASSED: one stable generated card; correct single-prompt batch; verified top-card key.')
                return card
            previous = key
        else:
            stable = 0
            previous = None
        page.wait_for_timeout(5000)
    capture_diagnostics(page, f'seq_wait_timeout_{number}')
    fail(f'VISUAL {number}: no single stable image appeared before timeout. No download/delete.')


# FLOW_FAST_OVERLAP_V1

def _fast_preview_without_stability_wait(page, image, card):
    """Capture target thumbnail from a viewport screenshot, avoiding element stability waits.

    Never associate an arbitrary screenshot with an image: verify the exact media
    key and ensure the entire card image lies on screen before cropping.
    """
    from io import BytesIO
    from PIL import Image

    # Gallery reflows during deletion; JS scrolling doesn't wait for a stable box.
    image.evaluate("el => el.scrollIntoView({block:'center', inline:'nearest', behavior:'instant'})")
    page.wait_for_timeout(250)
    def metadata():
        return image.evaluate("""el => {
            const r = el.getBoundingClientRect();
            return {key: el.currentSrc || el.src,
                    x:r.x, y:r.y, w:r.width, h:r.height,
                    viewportW:window.innerWidth, viewportH:window.innerHeight};
        }""")
    before = metadata()
    if before['key'] != card['key']:
        raise StaleFlowCard('Preview media changed before capture; no download attempted.')
    if (before['w'] < 100 or before['h'] < 80 or before['x'] < 0 or before['y'] < 0 or
        before['x'] + before['w'] > before['viewportW'] + 1 or
        before['y'] + before['h'] > before['viewportH'] + 1):
        raise StaleFlowCard('Preview card not fully visible; no download attempted.')
    # page.screenshot does NOT invoke locator.scroll_into_view_if_needed's
    # element stability wait that caused the original 30-second timeout.
    screenshot = page.screenshot(type='png', full_page=False, timeout=120000,
                                 animations='disabled')
    after = metadata()
    if after['key'] != before['key'] or any(abs(after[k]-before[k]) > 3 for k in ('x','y','w','h')):
        raise StaleFlowCard('Preview moved or changed during capture; no download attempted.')
    with Image.open(BytesIO(screenshot)) as full:
        sx = full.width / before['viewportW']
        sy = full.height / before['viewportH']
        bounds = (round(before['x']*sx), round(before['y']*sy),
                  round((before['x']+before['w'])*sx),
                  round((before['y']+before['h'])*sy))
        if (bounds[0] < 0 or bounds[1] < 0 or bounds[2] > full.width or
            bounds[3] > full.height or bounds[2] <= bounds[0] or bounds[3] <= bounds[1]):
            raise StaleFlowCard('Preview crop outside viewport; no download attempted.')
        region = full.crop(bounds)
        out = BytesIO()
        region.save(out, format='PNG')
        return out.getvalue()


def _fast_thumb(source):
    """Higher-detail fingerprint; 48x27 thumbnails caused identical scores."""
    from PIL import Image, ImageOps
    from io import BytesIO
    picture = Image.open(BytesIO(source) if isinstance(source, bytes) else source)
    with picture:
        return list(ImageOps.fit(picture.convert('RGB'), (192, 108),
                                 method=Image.Resampling.LANCZOS).getdata())


def _fast_difference(a, b):
    return sum(abs(x-y) for p,q in zip(a,b) for x,y in zip(p,q)) / (len(a)*3)


def _fast_assign(downloads, jobs):
    """Return (certain assignments, unresolved visuals), never guess or discard.

    Media ID is authoritative. Pixel similarity is accepted only for a clear
    two-sided unique match, not simply because a greedy matching chose it.
    """
    # Different Chrome download events can point to IDENTICAL bytes. Prevent a
    # duplicate event from receiving a second VISUAL identity. Unless a direct
    # Flow media ID can establish identity, leave the second record unassigned.
    from hashlib import sha256
    hashes = {}
    duplicate_indices = set()
    for i, rec in enumerate(downloads):
        path = rec.get('staged_path')
        if not path or not Path(path).is_file():
            continue
        digest = sha256(Path(path).read_bytes()).hexdigest()
        if digest in hashes:
            duplicate_indices.add(i)
            say(f'FAST: exact duplicate of staged event {hashes[digest]+1} found; '
                'will not blindly assign it to another VISUAL.')
        else:
            hashes[digest] = i
    assignments = {}
    used = set()
    for i, record in enumerate(downloads):
        source = ((record['download'].url or '') + ' ' +
                  (record['download'].suggested_filename or '')).lower()
        names = [n for n, job in jobs.items()
                 if job['media_id'] and job['media_id'] in source]
        if len(names) == 1 and names[0] not in assignments:
            assignments[names[0]] = i
            used.add(i)
            say(f'VISUAL {names[0]}: identified by exact Flow media ID.')
    remaining = {n for n in jobs if n not in assignments}
    unused = {i for i in range(len(downloads)) if i not in used and i not in duplicate_indices}
    # Recompute rankings after every accepted assignment. Require both the
    # visual's best file and the file's best visual to agree, with a clear margin.
    while remaining and unused:
        candidates=[]
        for n in sorted(remaining):
            ranked=sorted((_fast_difference(jobs[n]['preview'],downloads[i]['preview']),i)
                          for i in unused)
            dist,i=ranked[0]
            second_file=ranked[1][0] if len(ranked)>1 else 999
            other_visuals=sorted(_fast_difference(jobs[m]['preview'],downloads[i]['preview'])
                                 for m in remaining if m!=n)
            second_visual=other_visuals[0] if other_visuals else 999
            # At least 5 points' gap or a strong proportional margin on BOTH axes.
            margin=max(5, dist*.30)
            if (dist <= 35 and second_file-dist >= margin and
                    second_visual-dist >= margin):
                candidates.append((dist,n,i))
        if not candidates:
            break
        dist,n,i=min(candidates)
        assignments[n]=i
        remaining.remove(n)
        unused.remove(i)
        say(f'VISUAL {n}: high-resolution preview matched uniquely ({dist:.2f}).')
    return assignments, sorted(remaining)


def _fast_wait_for_gallery(page, expected):
    """No unconditional 90-second sleep: begin when all cards stay stable."""
    deadline = time.monotonic() + max(IMAGE_TIMEOUT*3, 600)
    last = None
    stable = 0
    while time.monotonic() < deadline:
        cards = _unique_gallery_cards(page)
        if len(cards) > expected:
            fail(f'Flow gallery has {len(cards)} cards; expected {expected}.')
        keys = frozenset(c['key'] for c in cards)
        stable = stable+1 if len(cards)==expected and keys==last else 1 if len(cards)==expected else 0
        if stable>=3:
            say(f'All {expected} Flow images ready; starting rapid 2K downloads.')
            return
        last = keys
        page.wait_for_timeout(2500)
    fail(f'Timeout: only {len(_unique_gallery_cards(page))}/{expected} image cards ready.')


def _fast_batch(flow, pending, journal):
    from uuid import uuid4
    folder = WORK / 'fast_batch_staging' / f'{pending[0].number:03d}-{pending[-1].number:03d}'
    folder.mkdir(parents=True, exist_ok=True)
    downloads = []
    jobs = {}
    def downloaded(event):
        downloads.append({'download':event})
        say(f'2K download started in Chrome ({len(downloads)}/{len(pending)}).')
    flow.on('download', downloaded)
    try:
        clear_flow_gallery(flow)
        say('FAST: submitting all prompts in reverse order (top card assumed newest).')
        for visual in reversed(pending):
            journal.put(visual.number, visual.hash, 'fast_queued', download_method='overlap')
            submit(flow, visual, journal)
        _fast_wait_for_gallery(flow, len(pending))
        for i, visual in enumerate(pending):
            remain = len(pending)-i
            if len(_unique_gallery_cards(flow)) != remain:
                fail(f'VISUAL {visual.number}: gallery count changed; stopped.')
            top = gallery_top_first(flow)
            if not top:
                fail(f'VISUAL {visual.number}: no visible top image.')
            card = top[0]
            image = find_image_locator(flow, card)
            preview_bytes = _fast_preview_without_stability_wait(flow, image, card)
            media = re.search(r'/image/([0-9a-f-]{36})', card['key'], re.I)
            jobs[visual.number] = {
                'visual':visual, 'key':card['key'],
                'media_id':media.group(1).lower() if media else None,
                'preview':_fast_thumb(preview_bytes)}
            (folder / f'visual_{visual.number:03d}_card_preview.png').write_bytes(preview_bytes)
            label = start_2k_in_background(flow, card, visual.number)
            jobs[visual.number]['quality'] = label
            journal.put(visual.number, visual.hash, 'fast_upscale_clicked',
                        image_key=card['key'], download_method='overlap')
            # Don't wait for download completion: the user tested that Flow
            # continues its already-launched 2K task after Move to trash.
            _trash_downloaded_top_card(flow, card, remain-1)
            journal.put(visual.number, visual.hash, 'fast_trashed_download_pending',
                        image_key=card['key'], download_method='overlap')
        say('FAST: all upscale clicks/trash actions finished. Now checking 2K files together.')
        # BATCH_MISSING_DOWNLOAD_RETRY_V2
        # Missing Chrome download events should not block the project for 15+ minutes.
        # This timeout begins AFTER all 2K requests and trash actions complete.
        import os
        event_wait = max(60, min(900, int(os.getenv('FLOW_DOWNLOAD_EVENT_WAIT_SECONDS', '180'))))
        until = time.monotonic() + event_wait
        last_report = 0
        while len(downloads) < len(pending) and time.monotonic() < until:
            flow.wait_for_timeout(2000)
            now = time.monotonic()
            if now - last_report >= 20:
                say(f'FAST: received {len(downloads)}/{len(pending)} download events; '
                    f'{max(0, int(until-now))}s before automatic retry.')
                last_report = now
        # Save all downloads that arrived, even if others never started. This
        # protects successful paid upscale requests from unnecessary retries.
        for i, record in enumerate(list(downloads)):
            try:
                ext = Path(record['download'].suggested_filename).suffix.lower()
                if ext not in ('.jpeg','.jpg','.png','.webp'): ext = '.image'
                staged = folder / f'{i+1:02d}_{uuid4().hex[:8]}{ext}'
                record['download'].save_as(str(staged))
                record['staged_path'] = staged
                if staged.stat().st_size < 10000:
                    raise ValueError('file unexpectedly small')
                record['preview'] = _fast_thumb(staged)
            except Exception as exc:
                say(f'FAST: download event {i+1} not usable ({str(exc)[:120]}).')
                record['preview'] = None
        good = [r for r in downloads if r.get('preview') is not None]
        # Always salvage individually confirmed files even if another image is
        # ambiguous. Never regenerate a batch to resolve a pixel similarity tie.
        assigned, unresolved = _fast_assign(good, jobs) if good else ({}, sorted(jobs))
        for n, index in sorted(assigned.items()):
            visual=jobs[n]['visual']
            rec=good[index]
            try:
                saved_path,width,height=save_verified(rec['download'], n, jobs[n]['quality'])
                journal.put(n, visual.hash, 'completed', file=saved_path,
                            width=width,height=height, quality_ui_label=jobs[n]['quality'],
                            image_key=jobs[n]['key'], download_method='fast_unique_match')
                say(f'VISUAL {n}: VERIFIED SAVED {Path(saved_path).name} ({width}x{height}).')
            except Exception as exc:
                unresolved.append(n)
                say(f'VISUAL {n}: saving verified match failed: {str(exc)[:150]}')
        unresolved=sorted(set(unresolved))
        # A persistent manifest survives process termination and identifies
        # remaining raw files; it is intentionally not a numbered assignment.
        import json
        manifest={
            'batch':[pending[0].number,pending[-1].number],
            'completed':sorted(n for n in jobs if n not in unresolved),
            'unresolved':unresolved,
            'staged_downloads':[str(r.get('staged_path','')) for r in good],
            'preview_files':[str(folder/f'visual_{n:03d}_card_preview.png') for n in jobs],
            'note':'Review ambiguous staged files against card previews. No paid regeneration is safe.'
        }
        (folder/'matching_review.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        if unresolved:
            for n in unresolved:
                visual=jobs[n]['visual']
                journal.put(n,visual.hash,'fast_staged_ambiguous',
                            download_method='overlap_staging',
                            reason='High-resolution similarity still ambiguous; raw files retained')
            raise RuntimeError(f'FAST: saved {len(assigned)-len(set(unresolved)&set(assigned))} certain images; '
                               f'VISUAL {unresolved} need staged-file review. '
                               f'FILES PRESERVED in {folder}; NO regeneration on resume.')
        say('FAST BATCH COMPLETE: all images verified and saved.')

    finally:
        flow.remove_listener('download', downloaded)



# RESUMABLE_SAFE_FLOW_V1
# All existing generated cards are recovered through their exact Reuse prompt.
# Never trash a card before its numbered, verified high-resolution file exists.
def _safe_cards_for_visuals(flow, visuals, wait_seconds=780, allow_partial=False):
    """Recover cards belonging to this batch; no new generation or deletion.

    When allow_partial is True, return a tuple (matched, waiting_numbers) instead of
    failing if some cards are still missing at the deadline.
    """
    waiting={v.number:v for v in visuals}
    matched={}
    deadline=time.monotonic()+wait_seconds
    next_report=0
    while waiting and time.monotonic()<deadline:
        candidates=gallery_bottom_first(flow)
        for candidate in candidates:
            if candidate['key'] in {c['key'] for c in matched.values()}:
                continue
            try:
                original=identify_by_reuse(flow,candidate,visuals)
            except Exception as exc:
                say(f'RESUME: card {candidate["key"][-32:]} not yet readable: {str(exc)[:110]}')
                continue
            if original is None:
                # Other images may be in this Flow project; never assign by order.
                continue
            previous=matched.get(original.number)
            if previous and previous['key']!=candidate['key']:
                fail(f'RESUME: two different Flow cards match VISUAL {original.number}. '
                     'Cannot choose automatically; no new image request sent.')
            matched[original.number]=candidate
            waiting.pop(original.number,None)
            say(f'RESUME: VISUAL {original.number} matched to existing Flow card by exact prompt.')
        if not waiting:
            break
        if time.monotonic()>=next_report:
            say(f'RESUME: waiting for existing generated cards {[n for n in sorted(waiting)]}; '
                'NO new generation submitted.')
            next_report=time.monotonic()+30
        flow.wait_for_timeout(5000)
    if waiting:
        capture_diagnostics(flow,'safe_resume_missing_flow_cards')
        if allow_partial:
            missing=sorted(waiting)
            say(f'RESUME: existing Flow cards not found within {wait_seconds}s for visuals {missing}. '
                'Any matched cards will be saved first; missing cards will be regenerated.')
            return matched, missing
        fail(f'SAFE RESUME: cannot verify existing Flow images for visuals {sorted(waiting)}. '
             'No duplicates were generated and no gallery cards were deleted. '
             'The images may still be processing, have been moved to the bin, or belong '
             'to a different Flow project. Review Flow. Only if they are truly gone, '
             'set FLOW_DISCARD_UNFINISHED=1 for ONE run to authorize regeneration.')
    return matched if not allow_partial else (matched, [])


def _reresolve_exact_flow_card(flow, visual, card, retries=3):
    """Reacquire a card that Flow recycled, never falling back to gallery order.

    Either find exactly the same media URL or reidentify the ORIGINAL full prompt.
    A different visual can never be substituted here.
    """
    for attempt in range(retries):
        try:
            find_image_locator(flow, card)
            return card
        except Exception:
            pass
        say(f'VISUAL {visual.number}: card moved in Flow; rescanning original prompt '
            f'({attempt+1}/{retries}), no regeneration.')
        choices=[]
        for candidate in gallery_bottom_first(flow):
            try:
                matched=identify_by_reuse(flow,candidate,[visual])
                if matched is not None and matched.number==visual.number:
                    choices.append(candidate)
            except Exception:
                continue
        unique={item['key']:item for item in choices}
        if len(unique)==1:
            card.clear()
            card.update(next(iter(unique.values())))
            try:
                find_image_locator(flow,card)
                say(f'VISUAL {visual.number}: exact original prompt reverified after Flow reflow.')
                return card
            except Exception:
                pass
        if len(unique)>1:
            fail(f'VISUAL {visual.number}: multiple cards have the same original prompt; '
                 'refusing ambiguous download.')
        flow.wait_for_timeout(1200)
    fail(f'VISUAL {visual.number}: exact original image is not currently accessible '
         'after card-rescan attempts; no wrong image downloaded and no new generation submitted.')


def _safe_snapshot(flow, card, visual=None):
    """Only use a screenshot from the exact live card, verified by media key."""
    last=None
    for _ in range(3):
        try:
            if visual is not None:
                _reresolve_exact_flow_card(flow,visual,card)
            image=find_image_locator(flow,card)
            return _fast_thumb(_fast_preview_without_stability_wait(flow,image,card))
        except Exception as exc:
            last=exc
            flow.wait_for_timeout(600)
    fail(f'Cannot verify Flow thumbnail for {card["key"][-40:]}: {last}')


def _safe_verify_file(path,expected_thumb,all_thumbs,number,media_id=None,download_identity=''):
    """Validate both actual pixels and 2K resolution; avoid misnumbered downloads."""
    from PIL import Image
    from agent import CONFIG
    with Image.open(path) as im:
        im.verify()
    with Image.open(path) as im:
        width,height=im.size
    if max(width,height)<int(CONFIG.get('minimum_download_width_or_height',1600)):
        fail(f'VISUAL {number}: {width}x{height} download too small; expected explicit 2K.')
    if path.stat().st_size<10000:
        fail(f'VISUAL {number}: image file unexpectedly small.')
    # Strongest proof: the actual Flow media UUID is in the download address.
    if media_id and media_id in (download_identity or '').lower():
        return width,height
    observed=_fast_thumb(path)
    own=_fast_difference(expected_thumb,observed)
    others=sorted(_fast_difference(preview,observed) for n,preview in all_thumbs.items() if n!=number)
    nearest=others[0] if others else 999
    if own>35 or (nearest<999 and nearest-own<max(5,own*.30)):
        fail(f'VISUAL {number}: downloaded image identity ambiguous ({own:.1f} '
             f'vs nearest other {nearest:.1f}). File remains staged; no incorrect numbering.')
    return width,height


def _safe_download(flow,visual,card,thumbs,journal):
    """Download a verified existing card; persist immediately, never touch the bin."""
    from uuid import uuid4
    from PIL import Image
    folder=WORK/'safe_download_staging'
    folder.mkdir(parents=True,exist_ok=True)
    number=visual.number
    expected=thumbs[number]
    mid=re.search(r'/image/([0-9a-f-]{36})',card['key'],re.I)
    media_id=mid.group(1).lower() if mid else None
    # A crash can happen AFTER the atomic image rename but BEFORE journal.put.
    # Reconcile that good local output first, without requesting another upscale.
    already_saved=[]
    for suffix in ('.png','.jpg','.jpeg','.webp'):
        final_file=OUTPUT/f'visual_{number:03d}{suffix}'
        if final_file.is_file():
            already_saved.append(final_file)
    if len(already_saved)>1:
        fail(f'VISUAL {number}: multiple local output files exist; no file overwritten.')
    if already_saved:
        final_file=already_saved[0]
        width,height=_safe_verify_file(final_file,expected,thumbs,number)
        journal.put(number,visual.hash,'completed',file=str(final_file),width=width,height=height,
                    image_key=card['key'],download_method='safe_local_file_recovery')
        say(f'VISUAL {number}: recovered verified existing local image {final_file.name}.')
        return
    existing=[]
    for suffix in ('.png','.jpg','.jpeg','.webp'):
        existing.extend(folder.glob(f'visual_{number:03d}_*{suffix}'))
    existing=sorted(existing)
    for staged in existing:
        try:
            width,height=_safe_verify_file(staged,expected,thumbs,number)
        except Exception:
            continue
        target=OUTPUT/f'visual_{number:03d}{staged.suffix.lower()}'
        if target.exists():
            fail(f'VISUAL {number}: already has output file {target}; manual verification needed.')
        OUTPUT.mkdir(parents=True,exist_ok=True)
        staged.replace(target)
        journal.put(number,visual.hash,'completed',file=str(target),width=width,height=height,
                    image_key=card['key'],download_method='safe_staged_recovery')
        say(f'VISUAL {number}: recovered staged 2K image {target.name} without another download.')
        return
    events=[]
    def received(download):
        events.append(download)
    flow.on('download',received)
    try:
        # Existing upscale_requested journal state is not evidence of a saved file.
        # We retry ONLY the existing card's download, never the generation itself.
        journal.put(number,visual.hash,'safe_2k_requested',image_key=card['key'])
        old_key=card['key']
        _reresolve_exact_flow_card(flow,visual,card)
        if card['key'] != old_key:
            thumbs[number]=_safe_snapshot(flow,card,visual)
            expected=thumbs[number]
            mid=re.search(r'/image/([0-9a-f-]{36})',card['key'],re.I)
            media_id=mid.group(1).lower() if mid else None
        candidate=find_image_locator(flow,card)
        current_key=candidate.evaluate('(el) => el.currentSrc || el.src')
        if current_key!=card['key']:
            fail(f'VISUAL {number}: Flow image changed before quality download; no click made.')
        received_download,label=download_quality_image(flow,card,events,number)
        ext=Path(received_download.suggested_filename).suffix.lower()
        if ext not in ('.png','.jpg','.jpeg','.webp'):
            ext='.png'
        staged=folder/f'visual_{number:03d}_{uuid4().hex[:12]}{ext}'
        received_download.save_as(str(staged))
        if received_download.failure():
            fail(f'VISUAL {number}: Chrome download failed: {received_download.failure()}')
        identity=((received_download.url or '')+' '+(received_download.suggested_filename or '')).lower()
        width,height=_safe_verify_file(staged,expected,thumbs,number,media_id,identity)
        final=OUTPUT/f'visual_{number:03d}{ext}'
        OUTPUT.mkdir(parents=True,exist_ok=True)
        if final.exists():
            fail(f'VISUAL {number}: output {final} already exists; refuse overwrite.')
        staged.replace(final)
        journal.put(number,visual.hash,'completed',file=str(final),width=width,height=height,
                    quality_ui_label=label,image_key=card['key'],download_method='safe_prompt_verified')
        say(f'VISUAL {number}: VERIFIED and SAVED {final.name} ({width}x{height}); Flow card preserved.')
    finally:
        flow.remove_listener('download',received)


def _safe_generate_or_resume(flow,pending,journal,may_regenerate=False):
    """Recover submitted Flow images; submit only genuinely never-submitted prompts."""
    known=[]
    new=[]
    for visual in pending:
        state=journal.item(visual.number) or {}
        if state.get('status') not in (None,'pending'):
            known.append(visual)
        else:
            new.append(visual)

    mapped={}
    if known:
        say(f'SAFE RESUME: {len(known)} previously submitted/uncertain image(s); '
            'recovering existing Flow cards without generating duplicates.')
        if may_regenerate:
            resume_wait = 60  # FLOW_AUTORECOVER_60S_V1: check for 60 seconds then retry missing only
            known_map, missing_known = _safe_cards_for_visuals(
                flow, known, wait_seconds=resume_wait, allow_partial=True)
            if known_map:
                # Save every recoverable existing card BEFORE clearing the Flow gallery.
                previews={v.number:_safe_snapshot(flow,known_map[v.number],v)
                          for v in known if v.number in known_map}
                for visual in known:
                    if visual.number in known_map:
                        _safe_download(flow,visual,known_map[visual.number],previews,journal)
                        mapped[visual.number]=known_map[visual.number]
            if missing_known:
                regen=[v for v in known if v.number in set(missing_known)]
                say(f'OPERATOR APPROVED: existing Flow cards were not found within {resume_wait}s for '
                    f'VISUAL {[v.number for v in regen]}. Preserving Flow gallery and regenerating only '
                    'those missing visuals after the 60-second recovery window.')
                # FLOW_RECOVERY_RETRY_ONCE_V2
                # A 60-second miss can be a slow or unreadable Flow card. Retry
                # at most ONCE per visual to prevent unlimited paid duplicates.
                # Preserve the previous durable entry in a separate audit file.
                from datetime import datetime as _retry_datetime, timezone as _retry_timezone
                import json as _retry_json
                import tempfile as _retry_tempfile
                recovery_dir = WORK / 'recovery_retry_archive'
                recovery_dir.mkdir(parents=True, exist_ok=True)
                for visual in regen:
                    prior = journal.item(visual.number) or {}
                    attempts = int(prior.get('auto_recovery_retry_count', 0))
                    if attempts >= 1:
                        fail(f'VISUAL {visual.number}: automatic retry already submitted once; '
                             'not spending another credit. Review its existing Flow card.')
                    # Do not retry an image that might already be downloaded.
                    local = [p for ext in ('.png', '.jpg', '.jpeg', '.webp')
                             for p in OUTPUT.glob(f'visual_{visual.number:03d}{ext}')
                             if p.is_file() and p.stat().st_size > 10000]
                    if local:
                        fail(f'VISUAL {visual.number}: existing local image {local[0]}; '
                             'refusing another paid request until journal reconciled.')
                    stamp = _retry_datetime.now(_retry_timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
                    archive = recovery_dir / f'visual_{visual.number:03d}_{stamp}.json'
                    # Exclusive-create: never overwrite a prior audit record.
                    with archive.open('x', encoding='utf-8') as save:
                        _retry_json.dump({'visual': visual.number, 'previous': prior,
                                          'reason': '60 second lookup could not verify existing Flow card',
                                          'possible_duplicate_generation': True}, save, indent=2)
                    journal.put(visual.number, visual.hash, 'retry_authorized',
                                auto_recovery_retry_count=attempts+1,
                                retry_archive=str(archive),
                                retry_reason='60s existing-card lookup failed; duplicate credit possible')
                    say(f'VISUAL {visual.number}: archived previous submitted state; '
                        'authorized ONE paid retry after 60 seconds.')
                new = regen + new
        else:
            mapped=_safe_cards_for_visuals(flow,known)
    else:
        # Only fresh batches clear the gallery. Previous batch downloads were
        # verified before pipeline advanced, so no unfinished batch is erased.
        clear_flow_gallery(flow)

    if new:
        say(f'SAFE: submitting {len(new)} NEW images; existing confirmed cards remain intact.')
        for visual in reversed(new):
            submit(flow,visual,journal)
        more=_safe_cards_for_visuals(flow,new)
        for n,card in more.items():
            if n in mapped and mapped[n]['key']!=card['key']:
                fail(f'VISUAL {n}: duplicate cards after submission; cannot pick one.')
            mapped[n]=card

    remaining=[v for v in pending if not already_done(v,journal)]
    if not remaining:
        say('SAFE BATCH COMPLETE: all original prompts and full-resolution files verified. '
            'No generated Flow images were moved to the bin during this batch.')
        return
    if not all(v.number in mapped for v in remaining):
        have=sorted(mapped)
        need=[v.number for v in remaining if v.number not in mapped]
        fail(f'SAFE: matched existing/generated cards {have} but still missing {need}; no numbering guessed.')
    # Capture all unresolved cards before downloads so opaque Chrome download URLs
    # can still be associated with the correct original visual using pixel similarity.
    previews={v.number:_safe_snapshot(flow,mapped[v.number],v) for v in remaining}
    for visual in remaining:
        _safe_download(flow,visual,mapped[visual.number],previews,journal)
    say('SAFE BATCH COMPLETE: all original prompts and full-resolution files verified. '
        'No generated Flow images were moved to the bin during this batch.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--max', type=int, default=15)
    args = parser.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    journal = Journal(WORK / 'state.json')
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CONFIG['cdp_url'], timeout=20000)
        try:
            context = browser.contexts[0]
            prompt_file=os.getenv('PIPELINE_PROMPTS_FILE','')
            if prompt_file:
                from prompts import extract_from_messages, verify_sequence
                path=Path(prompt_file)
                if not path.is_file():
                    fail(f'Saved prompt file not found: {path}. No Flow requests submitted.')
                prompts=extract_from_messages([path.read_text(encoding='utf-8-sig')])
                verify_sequence(prompts)
                say(f'SAFE: loaded {len(prompts)} verified ChatGPT prompts from saved file: {path.name}')
            else:
                prompts = find_chatgpt_prompts(context)
            flow = find_flow_page(context)
            flow.bring_to_front()
            selected=sorted(prompts,key=lambda v:v.number)[:max(1,min(args.max,15))]
            # Never regenerate a verified existing download.
            pending=[v for v in selected if not already_done(v,journal)]
            if not pending:
                say('All requested images already completed.')
                return
            # ORIGINAL FAST PERMANENT
            # The operator requested the classic FAST route as the default for
            # every normal batch run. This means:
            #   - clear the visible Flow gallery first
            #   - submit the current pending visuals rapidly
            #   - click 2K on the top card, trash it, move to the next card
            #   - verify the final downloads afterward
            # Existing incomplete journal entries are treated as restartable
            # pending visuals rather than a reason to force SAFE recovery.
            # Fully completed local files are still skipped by already_done().
            existing=[(v.number,(journal.item(v.number) or {}).get('status'))
                      for v in pending if (journal.item(v.number) or {}).get('status') not in (None,'pending')]
            force_original_fast = os.getenv('FLOW_ORIGINAL_FAST_ALWAYS','1') == '1' and not existing
            if force_original_fast and CONFIG.get('fast_download_trash_mode',True):
                # Do NOT interpret a previous submission as authorization to
                # clear the Flow gallery or pay for the same batch again.
                stage = WORK / 'fast_batch_staging' / f'{selected[0].number:03d}-{selected[-1].number:03d}'
                staged_files = ([f for f in stage.iterdir() if f.is_file() and
                                 f.suffix.lower() in ('.jpg','.jpeg','.png','.webp','.image')]
                                if stage.is_dir() else [])
                if existing or staged_files:
                    fail(f'FAST RESUME PROTECTION: previous batch has submission records or staged files. '
                         f'No gallery clearing or paid regeneration. Inspect {stage} and its '
                         'matching_review.json; recover staged downloads before attempting a NEW run.')
                if existing:
                    say(f'ORIGINAL FAST PERMANENT: {len(existing)} previously attempted image(s) '
                        f'in batch {selected[0].number}-{selected[-1].number}. '
                        'Clearing visible Flow cards and rerunning the current pending visuals with the original FAST route.')
                else:
                    say(f'ORIGINAL FAST PERMANENT: {len(pending)} fresh images '
                        f'{selected[0].number}-{selected[-1].number}; '
                        '2K click > trash > next, with final download verification.')
                _fast_batch(flow,pending,journal)
            elif existing:
                say(f'RECOVERY: {len(existing)} previously attempted images in '
                    f'batch {selected[0].number}-{selected[-1].number}. '
                    'Do not submit paid duplicates.')
                _safe_generate_or_resume(flow,pending,journal,
                    may_regenerate=True)  # FLOW_AUTORECOVER_60S_V1: 60s then retry missing only)
            elif CONFIG.get('fast_download_trash_mode',True):
                say(f'FAST15: {len(pending)} fresh images '
                    f'{selected[0].number}-{selected[-1].number}; '
                    '2K click > trash > next, with final download verification.')
                _fast_batch(flow,pending,journal)
            else:
                say('Safe mode selected in config.json.')
                _safe_generate_or_resume(flow,pending,journal)
        finally:
            try: browser.close()
            except Exception: pass

if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'\nSTOPPED: {exc}', file=sys.stderr)
        sys.exit(1)
