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
RETRIES = 2
TIMEOUT = 30


def ms_to_lrc_time(ms, bracket="square"):
    total_cs = max(0, int(round(float(ms) / 10)))
    minutes, remainder = divmod(total_cs, 6000)
    seconds, centis = divmod(remainder, 100)
    stamp = f"{minutes:02d}:{seconds:02d}.{centis:02d}"
    return f"[{stamp}]" if bracket == "square" else f"<{stamp}>"


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
            word_parts = []
            for w in words:
                w_text = (w.get("text") or "").strip()
                w_start = w.get("start")
                if w_text and w_start is not None:
                    word_parts.append(f"{ms_to_lrc_time(w_start, bracket='angle')}{w_text}")
                elif w_text:
                    word_parts.append(w_text)
            lrc_lines.append(f"{line_prefix}{' '.join(word_parts)}")
        else:
            text = (line.get("text") or "").strip()
            lrc_lines.append(f"{line_prefix}{text}")

    return "\n".join(lrc_lines) if lrc_lines else None


WORD_TAG_GAP = re.compile(r"(?<=[^\s\]\-])(?=<\d{1,3}:\d{2}[.:]\d{1,3}>)")


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
                print("psync rejected the token", file=sys.stderr)
                return None
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
            return None
        except (URLError, TimeoutError, OSError):
            if attempt < RETRIES:
                time.sleep(2 * (attempt + 1))
                continue
            return None

    return None


def fetch(track):
    title = (track.get("title") or "").strip()
    artist = (track.get("artist") or "").strip()
    album = (track.get("album") or "").strip()
    duration = track.get("duration") or 0

    if not title or not artist:
        return None

    params = {"title": title, "artist": artist, "format": "lrc"}

    if album:
        params["album"] = album

    if duration:
        params["duration"] = int(round(float(duration)))

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
    except Exception as error:
        print(f"psync error: {error}", file=sys.stderr)
        result = None
    if result is not None:
        json.dump(result, sys.stdout, ensure_ascii=False)
