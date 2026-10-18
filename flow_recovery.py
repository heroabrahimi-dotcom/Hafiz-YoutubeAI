"""Recover an already-generated Flow card when its old image URL goes stale.

The original full prompt is the ownership proof, not the gallery's position.
No clicks on Generate occur inside this module.
"""
from __future__ import annotations
import re
import time

MEDIA_ID = re.compile(r'/image/([0-9a-fA-F-]{36})', re.I)


def media_id(url):
    m = MEDIA_ID.search(url or '')
    return m.group(1).lower() if m else None


def unique_current_card(cards, old_key):
    """Find same image URL or stable media ID in currently rendered Flow cards."""
    exact = [c for c in cards if c['key'] == old_key]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return None
    mid = media_id(old_key)
    if mid is None:
        return None
    matched = [c for c in cards if media_id(c.get('key')) == mid]
    return matched[0] if len(matched) == 1 else None


def visible_candidates(page, fingerprint):
    """Use the real gallery image list. No nth-image identity assumptions."""
    result = []
    seen = set()
    for card in fingerprint(page):
        if card['key'] not in seen:
            result.append(card)
            seen.add(card['key'])
    return result


def recover_missing(page, visuals, journal, fingerprint, identify, logger, timeout=90):
    """Recover missing cards by matching their original full Reuse prompt.

    Leaves unresolved visuals unmodified. The caller decides whether to
    regenerate a missing image, never this function.
    """
    outstanding = {v.number: v for v in visuals}
    if not outstanding:
        return {}
    all_pending = list(outstanding.values())
    known = {}
    scanned = set()
    for v in all_pending:
        row = journal.item(v.number) or {}
        candidate = unique_current_card(visible_candidates(page, fingerprint), row.get('image_key'))
        if candidate:
            known[v.number] = candidate
            outstanding.pop(v.number)
            if row.get('image_key') != candidate['key']:
                journal.put(v.number, v.hash, 'image_ready', image_key=candidate['key'])
                logger(f'VISUAL {v.number}: refreshed old Flow card reference')
    if not outstanding:
        return known

    def scan_once():
        # Also scan any cards that are already owned by an old stale key:
        # ownership is re-validated using the original full Reuse prompt.
        for card in visible_candidates(page, fingerprint):
            if not outstanding:
                return
            key = card['key']
            if key in scanned:
                continue
            scanned.add(key)
            try:
                matched = identify(page, card, all_pending)
            except Exception as exc:
                # Flow renders gallery cards asynchronously. A fingerprint
                # may disappear between the scan and opening its Reuse menu.
                # Skip only that candidate, not the entire recovery scan.
                if isinstance(exc, RuntimeError) and (
                    exc.__class__.__name__ == 'StaleFlowCard' or
                    'Target image missing or duplicated' in str(exc)
                ):
                    continue
                raise
            if not matched or matched.number not in outstanding:
                continue
            row = journal.item(matched.number) or {}
            journal.put(matched.number, matched.hash, 'image_ready',
                        image_key=key,
                        preview_dimensions=[card['width'], card['height']],
                        recovered_previous_key=row.get('image_key'))
            known[matched.number] = card
            outstanding.pop(matched.number, None)
            logger(f'VISUAL {matched.number}: recovered gallery card by original prompt')

    # Avoid expensive inspection for a key that simply changed query strings.
    # Scan available thumbnails, then reload once in case Flow virtualized the grid.
    deadline = time.monotonic() + timeout
    try:
        scan_once()
        if outstanding and time.monotonic() < deadline:
            page.reload(wait_until='domcontentloaded', timeout=45000)
            page.wait_for_timeout(1200)
            for v in tuple(outstanding.values()):
                row = journal.item(v.number) or {}
                candidate = unique_current_card(visible_candidates(page, fingerprint), row.get('image_key'))
                if candidate:
                    journal.put(v.number, v.hash, 'image_ready', image_key=candidate['key'])
                    known[v.number] = candidate
                    outstanding.pop(v.number)
            if outstanding:
                scan_once()
    except Exception as exc:
        logger(f'Flow gallery recovery incomplete ({str(exc)[:130]})')
    return known
