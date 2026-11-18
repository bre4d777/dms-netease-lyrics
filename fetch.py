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


def call(params):
    query = urlencode(params)
    headers = {"Accept": "text/plain", "User-Agent": "psync-client"}

    for attempt in range(RETRIES + 1):
        if TOKEN:
            headers["Authorization"] = f"Bearer {TOKEN}"

        request = Request(f"{BASE_URL}/api/lyrics?{query}", headers=headers)

        try:
            with urlopen(request, timeout=TIMEOUT) as response:
                return response.read().decode("utf-8", "replace")
        except HTTPError as error:
            if error.code == 404:
                return None
            if error.code == 401:
                raise ValueError("psync rejected the token")
            if error.code == 429 and attempt < RETRIES:
                wait = float(error.headers.get("Retry-After") or 2)
                time.sleep(min(wait, 10))
                continue
            if error.code >= 500 and attempt < RETRIES:
                time.sleep(2 * (attempt + 1))
                continue
            return None
        except (URLError, TimeoutError):
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

    lyrics = call(params)

    if not lyrics or not LINE_TIMESTAMP.search(lyrics):
        return None

    return {"format": "lrc", "lyrics": lyrics}


if __name__ == "__main__":
    result = fetch(json.load(sys.stdin))
    if result is not None:
        json.dump(result, sys.stdout, ensure_ascii=False)
