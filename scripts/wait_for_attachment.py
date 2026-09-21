#!/usr/bin/env python3
"""Wait for one exact top-level Notes audio attachment without opening Notes."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

from plan import NOTES_DB, load_notes, normalized_title


def check_attachment(notes_db: Path, note_pk: int, title: str, duration_seconds: int) -> dict:
    _notes, attachments = load_notes(notes_db)
    expected = (normalized_title(title), duration_seconds)
    matches = [
        item
        for item in attachments.get(note_pk, [])
        if (item["normalized_title"], item["seconds"]) == expected
    ]
    if len(matches) == 1:
        return {"status": "complete", "attachment_pk": matches[0]["pk"]}
    if len(matches) > 1:
        return {"status": "duplicate", "attachment_pks": [item["pk"] for item in matches]}
    return {"status": "pending"}


def wait_for_attachment(
    notes_db: Path,
    note_pk: int,
    title: str,
    duration_seconds: int,
    timeout: float,
    interval: float,
) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        result = check_attachment(notes_db, note_pk, title, duration_seconds)
        if result["status"] != "pending":
            return result
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {"status": "timeout"}
        time.sleep(min(interval, remaining))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--note-pk", type=int, required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--duration-seconds", type=int, required=True)
    parser.add_argument("--notes-db", type=Path, default=NOTES_DB)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--interval", type=float, default=0.5)
    args = parser.parse_args()
    try:
        result = wait_for_attachment(
            args.notes_db,
            args.note_pk,
            args.title,
            args.duration_seconds,
            max(0.0, args.timeout),
            max(0.05, args.interval),
        )
    except (FileNotFoundError, RuntimeError, ValueError, sqlite3.Error) as error:
        result = {"status": "fatal", "reason": type(error).__name__, "detail": str(error)}
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result["status"] == "complete" else 1)


if __name__ == "__main__":
    main()
