#!/usr/bin/env python3

import json
import os
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE_URL = os.environ.get("PSYNC_URL", "https://getpsync.vercel.app").rstrip("/")
TOKEN = os.environ.get("PSYNC_TOKEN", "psync@prayag").strip()

LINE_TIMESTAMP = re.compile(r"^\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]", re.MULTILINE)
CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")
RETRIES = 0
TIMEOUT = 7  # Go kills the command at 8s


class PsyncError(Exception):
    """Lookup failed for a reason that says nothing about the song, so it must not be cached as a miss."""


def ms_to_lrc_time(ms, bracket="square"):
    total_cs = max(0, round(float(ms) / 10))
    minutes, remainder = divmod(total_cs, 6000)
    seconds, centis = divmod(remainder, 100)
    stamp = f"{minutes:02d}:{seconds:02d}.{centis:02d}"
    return f"[{stamp}]" if bracket == "square" else f"<{stamp}>"


def spaces_from_text(stripped, text):
    """Use the line's own text to see which word boundaries really have a space."""
    low = text.lower()
    pos = 0
    spans = []
    for w in stripped:
        idx = low.find(w.lower(), pos)
        if idx == -1:
            return None
        spans.append((idx, idx + len(w)))
        pos = idx + len(w)
    return [
        any(c.isspace() for c in text[spans[i][1]:spans[i + 1][0]])
        for i in range(len(spans) - 1)
    ]


def words_to_lrc(words, line_text=""):
    parts = []
    for w in words:
        raw = w.get("text") or ""
        if not raw.strip():
            continue
        start = w.get("start")
        tag = ms_to_lrc_time(start, bracket="angle") if start is not None else ""
        parts.append((tag, raw))

    if not parts:
        return ""

    # does the server give us real whitespace between words?
    has_ws = any(r[-1].isspace() for _, r in parts[:-1]) or any(
        r[0].isspace() for _, r in parts[1:]
    )

    spaces = None
    if not has_ws and line_text:
        spaces = spaces_from_text([r.strip() for _, r in parts], line_text)

    out = []
    for i, (tag, raw) in enumerate(parts):
        stripped = raw.strip()
        lead = raw[: len(raw) - len(raw.lstrip())]
        trail = raw[len(raw.rstrip()):]
        out.append(f"{lead}{tag}{stripped}{trail}")

        if i < len(parts) - 1 and not has_ws:
            nxt = parts[i + 1][1].strip()
            if spaces is not None:
                if spaces[i]:
                    out.append(" ")
            elif not (CJK.match(stripped[-1]) and CJK.match(nxt[0])):
                out.append(" ")

    return "".join(out).rstrip()


def json_to_lrc(data, enable_word_sync=True):
    lines = data.get("lines")
    if not isinstance(lines, list) or not lines:
        return None

    is_word_sync = data.get("kind") == "word" and enable_word_sync
    lrc_lines = []

    for line in lines:
        start = line.get("start")
        if start is None:
            continue

        line_prefix = ms_to_lrc_time(start, bracket="square")
        words = line.get("words")

        if is_word_sync and isinstance(words, list) and words:
            lrc_lines.append(f"{line_prefix}{words_to_lrc(words, line.get('text') or '')}")
        else:
            text = (line.get("text") or "").strip()
            lrc_lines.append(f"{line_prefix}{text}")

    return "\n".join(lrc_lines) if lrc_lines else None


WORD_TAG_GAP = re.compile(
    r"(?<=[^\s\]\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af])(?=<\d{1,3}:\d{2}[.:]\d{1,3}>)"
)


def space_words(lrc):
    return WORD_TAG_GAP.sub(" ", lrc)


def call(params):
    query = urlencode(params)
    headers = {
        "Accept": "application/json, text/plain;q=0.9",
        "User-Agent": "psync-client",
    }
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"

    for attempt in range(RETRIES + 1):
        request = Request(f"{BASE_URL}/api/lyrics?{query}", headers=headers)

        try:
            with urlopen(request, timeout=TIMEOUT) as response:
                return response.read().decode("utf-8", "replace")
        except HTTPError as error:
            if error.code == 401:
                raise PsyncError("psync rejected the token") from error
            if error.code == 429 and attempt < RETRIES:
                try:
                    wait = float(error.headers.get("Retry-After") or 2)
                except ValueError:
                    wait = 2
                time.sleep(min(wait, 10))
                continue
            if error.code >= 500 and attempt < RETRIES:
                time.sleep(2 * (attempt + 1))
                continue
            if error.code == 429 or error.code >= 500:
                raise PsyncError(f"HTTP {error.code}") from error
            return None
        except (URLError, TimeoutError, OSError) as error:
            if attempt < RETRIES:
                time.sleep(2 * (attempt + 1))
                continue
            raise PsyncError(f"network error: {error}") from error

    return None


def fetch(track):
    title = (track.get("title") or "").strip()
    artist = (track.get("artist") or "").strip()
    album = (track.get("album") or "").strip()
    duration = track.get("duration") or 0

    if not title or not artist:
        return None

    # json so the server's real word spacing survives
    params: dict[str, str | int] = {"title": title, "artist": artist, "format": "json"}

    if album:
        params["album"] = album

    if duration:
        params["duration"] = round(float(duration))

    raw = call(params)
    if not raw:
        return None

    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            lyrics = json_to_lrc(data, enable_word_sync=True)
            if lyrics and LINE_TIMESTAMP.search(lyrics):
                return {"format": "lrc", "lyrics": lyrics}
    except json.JSONDecodeError:
        pass

    if LINE_TIMESTAMP.search(raw):
        return {"format": "lrc", "lyrics": space_words(raw)}

    return None


if __name__ == "__main__":
    try:
        result = fetch(json.load(sys.stdin))
    except (PsyncError, OSError, ValueError, TypeError, AttributeError) as error:
        print(f"psync error: {error}", file=sys.stderr)
        sys.exit(1)
    if result is not None:
        json.dump(result, sys.stdout, ensure_ascii=False)
