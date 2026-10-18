"""Strictly extract numbered Nano Banana prompts from ChatGPT messages."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import re

# A heading must occupy a line; this avoids incidental references to 'Visual 3'.
HEADING = re.compile(
    r'(?im)^\s*(?:#{1,6}\s*)?(?:\*\*)?VISUAL\s+(\d+)(?:\*\*)?(?:\s*[-—:]\s*[^\n]*)?\s*$'
)
PROMPT_LABEL = re.compile(
    r'(?im)^\s*(?:#{1,6}\s*)?(?:\*\*)?(?:(?:GOOGLE\s+)?NANO\s+BANANA\s+)?PROMPT(?:\*\*)?\s*:\s*(.*)$'
)
OTHER_LABEL = re.compile(
    r'(?im)^\s*(?:#{1,6}\s*)?(?:\*\*)?(?:SCRIPT(?:\s+SENTENCE)?|VISUAL\s+PURPOSE|PURPOSE|NOTES)(?:\*\*)?\s*:'
)

@dataclass(frozen=True)
class VisualPrompt:
    number: int
    prompt: str
    hash: str


def _clean_prompt(value: str) -> str:
    value = value.strip()
    value = re.sub(r'^```(?:text|plaintext|prompt)?\s*\n', '', value, flags=re.I)
    value = re.sub(r'\n```\s*$', '', value)
    # A visual's actual prompt ends at its first blank line. Exclude
    # reference-image captions, links, and explanations that follow it.
    value = re.split(r'\n\s*\n', value, maxsplit=1)[0]
    return value.strip().strip('“”\"').strip()


def extract_from_messages(messages: list[str]) -> list[VisualPrompt]:
    """Accept only assistant-authored response text supplied by browser extraction.

    A repeated visual number with a different prompt is an error rather than a guess.
    """
    found: dict[int, VisualPrompt] = {}
    for message in messages:
        matches = list(HEADING.finditer(message))
        for i, match in enumerate(matches):
            number = int(match.group(1))
            block = message[match.end() : matches[i + 1].start() if i + 1 < len(matches) else len(message)]
            label = PROMPT_LABEL.search(block)
            if not label:
                continue
            end_match = OTHER_LABEL.search(block, label.end())
            raw = (label.group(1) + '\n' + block[label.end(): end_match.start() if end_match else len(block)])
            prompt = _clean_prompt(raw)
            # Do not accept empty prompts or a placeholder from the guide.
            if len(prompt) < 35 or re.search(r'(?i)^\[\s*(?:paste|detailed|image)\s+', prompt):
                continue
            entry = VisualPrompt(number=number, prompt=prompt, hash=hashlib.sha256(prompt.encode('utf-8')).hexdigest())
            if number in found and found[number].hash != entry.hash:
                raise ValueError(f'VISUAL {number} appears with conflicting prompt contents. Open one clear source conversation.')
            found[number] = entry
    return [found[n] for n in sorted(found)]


def verify_sequence(prompts: list[VisualPrompt]):
    if not prompts:
        raise ValueError('No VISUAL prompts with NANO BANANA PROMPT: were found in ChatGPT assistant responses.')
    indexes = [p.number for p in prompts]
    # The first may be VISUAL 6 when the user's chosen conversation starts with NEXT 5.
    missing = set(range(indexes[0], indexes[-1] + 1)) - set(indexes)
    if missing:
        raise ValueError(f'Missing visual numbers: {sorted(missing)}. Refusing to skip silently.')
