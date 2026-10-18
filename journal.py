"""Crash-safe Flow image journal with Windows-friendly atomic writes.

This file is used across all AncientYouTubeAuto projects. It never deletes or
resets an existing state.json and never re-submits image requests.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

# A transient Windows file-lock (Defender/indexing/another reader) can block
# os.replace even after the preceding write has succeeded.
REPLACE_RETRY_SECONDS = 25.0
EVENT_RETRY_SECONDS = 3.0


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class Journal:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding='utf-8-sig'))
            if self.data.get('version') != 1:
                raise ValueError('Unsupported journal version')
        else:
            self.data = {'version': 1, 'items': {}}
        self.log_path = self.path.with_name('activity.jsonl')

    def item(self, number):
        return self.data['items'].get(str(number))

    def put(self, number, digest, status, **attrs):
        key = str(number)
        old = self.data['items'].get(key, {})
        if old and old['hash'] != digest:
            raise ValueError(
                f'VISUAL {number}: prompt changed since last run. Refusing to reuse the old result. '
                'Back up or manually review the state file before running again.'
            )
        item = {**old, 'hash': digest, 'status': status, 'updated': utc_now(), **attrs}
        self.data['items'][key] = item
        # Save the resume journal FIRST. Audit logging is supplementary and must
        # never change the outcome of an already durable state transition.
        self._save()
        try:
            self.event(number, status, **attrs)
        except OSError as exc:
            print(f'WARNING: state.json saved but optional activity log unavailable: {exc}',
                  file=sys.stderr, flush=True)
        return item

    def event(self, number, action, **attrs):
        record = json.dumps({'time': utc_now(), 'visual': number, 'event': action, **attrs},
                            ensure_ascii=False) + '\n'
        deadline = time.monotonic() + EVENT_RETRY_SECONDS
        while True:
            try:
                with self.log_path.open('a', encoding='utf-8') as file:
                    file.write(record)
                    file.flush()
                return
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.2)

    def _save(self):
        # A distinct temp filename prevents a stale state.tmp or concurrent
        # writer from colliding with our in-progress write.
        path = self.path
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode='w', encoding='utf-8', dir=path.parent,
                prefix=f'.{path.stem}.{os.getpid()}.', suffix='.tmp',
                delete=False
            ) as file:
                temp_path = Path(file.name)
                json.dump(self.data, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())

            deadline = time.monotonic() + REPLACE_RETRY_SECONDS
            attempt = 0
            while True:
                try:
                    os.replace(temp_path, path)
                    temp_path = None
                    return
                except PermissionError as exc:
                    # Never truncate or delete the existing state.json to work
                    # around an OS lock; retry the atomic rename only.
                    attempt += 1
                    if time.monotonic() >= deadline:
                        raise PermissionError(
                            f'Windows could not replace {path.name} after {attempt} attempts. '
                            f'Existing journal is untouched. Check antivirus, file permissions, '
                            f'and whether another copy of the bot is running. Original error: {exc}'
                        ) from exc
                    if attempt == 1:
                        print(f'Flow journal temporarily locked; retrying atomic save...',
                              file=sys.stderr, flush=True)
                    time.sleep(min(1.0, 0.15 + attempt * 0.1))
        finally:
            # Best-effort cleanup of *our* temp file only. Never delete
            # state.json, historic state.tmp, images, or project metadata.
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
