"""Batch-by-batch ChatGPT -> Google Flow loop; requires working agent.py.

Only posts NEXT 5 after every visual in the preceding reply is journaled
completed with an existing image file. Stops on ambiguity or any failure.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright
from agent import CONFIG, WORK, read_latest_assistant_with_pc_bot_fallback
from prompts import extract_from_messages, verify_sequence

BASE = Path(__file__).resolve().parent

def log(s):
    print(time.strftime('[%H:%M:%S]'), s, flush=True)

def latest(page):
    result = read_latest_assistant_with_pc_bot_fallback(page)
    return (result.get('latest') or '').strip()

def prompts_of(text):
    prompts = extract_from_messages([text])
    if prompts:
        verify_sequence(prompts)
    return prompts

def choose_chat(context, specified):
    tabs = [p for p in context.pages if p.url.startswith(('https://chatgpt.com/c/', 'https://chat.openai.com/c/'))]
    if specified:
        tabs = [p for p in tabs if p.url.split('?')[0] == specified.split('?')[0]]
    if len(tabs) != 1:
        raise RuntimeError(f'Expected exactly one target ChatGPT conversation; found {len(tabs)}. Close extra ChatGPT conversations or pass --chat-url.')
    return tabs[0]

def checked_completed(items):
    path = WORK / 'state.json'
    if not path.exists():
        raise RuntimeError('No state.json after running the image agent.')
    state = json.loads(path.read_text(encoding='utf-8-sig')).get('items', {})
    for p in items:
        record = state.get(str(p.number))
        if not record or record.get('hash') != p.hash or record.get('status') != 'completed':
            raise RuntimeError(f'VISUAL {p.number} is not verified completed. No NEXT message sent.')
        file = Path(record.get('file', ''))
        if not file.is_file() or file.stat().st_size <= 0:
            raise RuntimeError(f'VISUAL {p.number} download missing/empty: {file}. No NEXT message sent.')

def run_batch(items):
    log(f'Running Flow agent for VISUAL {items[0].number}–{items[-1].number}')
    # Each call uses the existing agent's latest-reply reader, journal and safeguards.
    code = subprocess.run([sys.executable, str(BASE/'batch_agent.py'), '--max', '5'], cwd=BASE, check=False).returncode
    if code:
        raise RuntimeError(f'Image agent stopped (exit code {code}). No NEXT message sent; inspect diagnostics.')
    checked_completed(items)
    log('All images for this batch are verified on disk.')

def send_next(page, next_number):
    # Confirm correct conversation and find a single composer; never touch Flow.
    selectors = [page.locator('#prompt-textarea'), page.locator('[contenteditable="true"][data-testid="composer-text-input"]'),
                 page.locator('[contenteditable="true"][role="textbox"]')]
    editor = next((e for s in selectors for e in [s.last] if e.count() and e.is_visible()), None)
    if editor is None:
        raise RuntimeError('Cannot find ChatGPT message composer. No message sent.')
    message = (f'NEXT 5. Continue with VISUAL {next_number} through VISUAL {next_number+4}. '
               'For each visual include its number and a full NANO BANANA PROMPT: or Prompt: field. '
               'Keep the exact established cartoon style, continue the same script in order, '
               'and do not repeat previous visuals.')
    editor.fill(message)
    send = page.locator('button[data-testid="send-button"]')
    if not (send.count() and send.first.is_visible() and send.first.is_enabled()):
        send = page.get_by_role('button', name='Send prompt')
    if not (send.count() and send.first.is_visible() and send.first.is_enabled()):
        send = page.get_by_role('button', name='Send message')
    if send.count() and send.first.is_visible() and send.first.is_enabled():
        send.first.click()
    else:
        # Enter can submit on the ChatGPT composer; do not retry if ambiguous.
        editor.press('Enter')
    log(f'Sent NEXT 5, requesting VISUAL {next_number}–{next_number+4}.')

def wait_new_batch(page, old_digest, expected_start, timeout):
    until = time.monotonic() + timeout
    last = None
    stable_since = None
    while time.monotonic() < until:
        time.sleep(3)
        try:
            new = latest(page)
            if not new or hashlib.sha256(new.encode()).hexdigest() == old_digest:
                continue
            batch = prompts_of(new)
            if not batch or batch[0].number != expected_start:
                continue
            if len(batch) != 5 or batch[-1].number != expected_start+4:
                continue
            h = hashlib.sha256(new.encode()).hexdigest()
            if h != last:
                stable_since = time.monotonic()
                last = h
            if time.monotonic()-stable_since >= 12:
                log(f'New stable ChatGPT reply contains VISUAL {batch[0].number}–{batch[-1].number}.')
                return batch
        except Exception as exc:
            log(f'Waiting for next reply: {exc}')
    raise RuntimeError('Timed out waiting for the complete, correctly numbered NEXT 5 reply. No images generated for this new batch.')

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--max-batches', type=int, default=0, help='0 = keep looping until Ctrl+C, an error, or no more batches')
    parser.add_argument('--chat-url', default='', help='Exact target ChatGPT conversation URL, optional')
    parser.add_argument('--reply-timeout', type=int, default=240)
    args=parser.parse_args()
    with sync_playwright() as pw:
        browser=pw.chromium.connect_over_cdp(CONFIG['cdp_url'], timeout=20000)
        try:
            if not browser.contexts:
                raise RuntimeError('No browser context attached.')
            page=choose_chat(browser.contexts[0], args.chat_url)
            initial=latest(page)
            batch=prompts_of(initial)
            if not batch:
                raise RuntimeError('Latest assistant reply has no numbered image prompts. Open the desired reply before starting.')
            log(f'Started in {page.url}. Current reply VISUAL {batch[0].number}–{batch[-1].number}.')
            count=0
            while True:
                run_batch(batch)
                count += 1
                if args.max_batches and count >= args.max_batches:
                    log(f'Stopped after {count} batch(es) as requested.')
                    return
                # Important: don't send NEXT if assistant reply changed while producing images.
                current=latest(page)
                if hashlib.sha256(current.encode()).hexdigest() != hashlib.sha256(initial.encode()).hexdigest():
                    raise RuntimeError('Latest ChatGPT reply changed unexpectedly during Flow generation. Stopping to prevent skipping prompts.')
                next_number=batch[-1].number+1
                digest=hashlib.sha256(initial.encode()).hexdigest()
                send_next(page, next_number)
                batch=wait_new_batch(page, digest, next_number, args.reply_timeout)
                initial=latest(page)
        finally:
            browser.close()

if __name__=='__main__':
    try: main()
    except KeyboardInterrupt: print('\nStopped by user (Ctrl+C).')
    except Exception as exc:
        print(f'\nSTOPPED: {exc}', file=sys.stderr)
        sys.exit(1)
