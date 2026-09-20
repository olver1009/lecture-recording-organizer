#!/usr/bin/env python3
"""Build a read-only Voice Memos -> Notes action plan.

The caller supplies the current Voice Memos "모든 녹음 항목" AX rows. The
script intersects those rows with the databases, applies the fixed timetable,
and returns only the UI mutations and reports that are still needed.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo


APPLE_UNIX_OFFSET = 978_307_200
SEOUL = ZoneInfo("Asia/Seoul")
START_DATE = date(2026, 9, 1)
END_DATE = date(2026, 12, 18)

VOICE_DB = Path.home() / "Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings/CloudRecordings.db"
NOTES_DB = Path.home() / "Library/Group Containers/group.com.apple.notes/NoteStore.sqlite"

SCHEDULE = {
    0: [("09:00", "10:30", "미주지역지리"), ("13:00", "15:00", "지도학및실습"), ("16:30", "18:00", "도시지리학특강")],
    1: [("15:00", "16:30", "응용 지형학"), ("16:30", "18:00", "기후변화와 미래환경")],
    2: [("10:30", "12:00", "미주지역지리"), ("13:00", "15:00", "지도학및실습"), ("15:00", "16:30", "도시지리학특강")],
    3: [("15:00", "16:30", "응용 지형학"), ("16:30", "18:00", "기후변화와 미래환경")],
}

NOTE_FOLDERS = {
    "미주지역지리": "미주지역지리",
    "지도학및실습": "지도학",
    "도시지리학특강": "도시지리특강",
    "응용 지형학": "응용지형학",
    "기후변화와 미래환경": "기후변화",
}


def emit(payload: dict, exit_code: int = 0) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    raise SystemExit(exit_code)


def ro_connect(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path)
    uri = f"file:{quote(str(path), safe='/')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def normalized_title(value: str | None) -> str:
    text = unicodedata.normalize("NFC", value or "").strip()
    text = re.sub(r"\.m4a$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<=\d)[:：](?=\d)", "/", text, count=1)
    return re.sub(r"\s+", " ", text).strip()


def rounded_seconds(value: float | int) -> int:
    return int(float(value) + 0.5)


def parse_duration(value: object) -> int:
    if isinstance(value, (int, float)):
        return rounded_seconds(value)
    text = str(value or "").strip().replace(" ", "")
    if not text:
        raise ValueError("empty duration")
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return rounded_seconds(float(text))
    clock = re.fullmatch(r"(?:(\d+):)?(\d+):(\d+)", text)
    if clock:
        hours = int(clock.group(1) or 0)
        return hours * 3600 + int(clock.group(2)) * 60 + int(clock.group(3))
    hours = re.search(r"(\d+)시간", text)
    minutes = re.search(r"(\d+)분", text)
    seconds = re.search(r"(\d+)초", text)
    if not any((hours, minutes, seconds)):
        raise ValueError(f"unsupported duration: {value}")
    return int(hours.group(1) if hours else 0) * 3600 + int(minutes.group(1) if minutes else 0) * 60 + int(seconds.group(1) if seconds else 0)


def parse_hhmm(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


def classify(started: datetime) -> str | None:
    classes = SCHEDULE.get(started.weekday(), [])
    current = started.time().replace(tzinfo=None)
    core = [course for start, end, course in classes if parse_hhmm(start) <= current < parse_hhmm(end)]
    if len(core) == 1:
        return core[0]
    if core:
        return None
    early = []
    for start, _end, course in classes:
        class_start = datetime.combine(started.date(), parse_hhmm(start), SEOUL)
        if class_start - timedelta(minutes=15) <= started < class_start:
            early.append(course)
    return early[0] if len(early) == 1 else None


def load_voice_rows(path: Path, today: date) -> tuple[list[dict], set[tuple[str, int]]]:
    with ro_connect(path) as db:
        rows = db.execute(
            """
            SELECT Z_PK, ZUNIQUEID, ZCUSTOMLABELFORSORTING, ZPATH, ZDATE, ZDURATION
            FROM ZCLOUDRECORDING
            WHERE ZPATH IS NOT NULL
            ORDER BY ZDATE
            """
        ).fetchall()
    result = []
    ignored: set[tuple[str, int]] = set()
    upper = min(today, END_DATE)
    for row in rows:
        title = row["ZCUSTOMLABELFORSORTING"] or ""
        seconds = rounded_seconds(row["ZDURATION"] or 0)
        key = (normalized_title(title), seconds)
        if float(row["ZDURATION"] or 0) <= 600:
            ignored.add(key)
            continue
        started = datetime.fromtimestamp(float(row["ZDATE"]) + APPLE_UNIX_OFFSET, timezone.utc).astimezone(SEOUL)
        if not (START_DATE <= started.date() <= upper):
            ignored.add(key)
            continue
        if not (time(9, 0) <= started.time().replace(tzinfo=None) < time(18, 0)):
            ignored.add(key)
            continue
        result.append(
            {
                "z_pk": int(row["Z_PK"]),
                "unique_id": row["ZUNIQUEID"],
                "current_title": title,
                "path": row["ZPATH"],
                "started": started,
                "duration": float(row["ZDURATION"]),
                "seconds": seconds,
                "course": classify(started),
            }
        )
    return result, ignored


def entity_ids(db: sqlite3.Connection) -> dict[str, int]:
    return {row["Z_NAME"]: int(row["Z_ENT"]) for row in db.execute("SELECT Z_ENT, Z_NAME FROM Z_PRIMARYKEY")}


def load_notes(path: Path) -> tuple[dict[tuple[str, str], list[int]], dict[int, list[dict]]]:
    with ro_connect(path) as db:
        entities = entity_ids(db)
        required = {"ICAttachment", "ICNote", "ICFolder"}
        if not required.issubset(entities):
            raise RuntimeError(f"Notes schema missing entities: {sorted(required - entities.keys())}")
        folders = {
            int(row["Z_PK"]): normalized_title(row["ZTITLE2"])
            for row in db.execute(
                "SELECT Z_PK, ZTITLE2 FROM ZICCLOUDSYNCINGOBJECT WHERE Z_ENT=? AND COALESCE(ZMARKEDFORDELETION,0)=0",
                (entities["ICFolder"],),
            )
        }
        notes: dict[tuple[str, str], list[int]] = defaultdict(list)
        for row in db.execute(
            "SELECT Z_PK, ZTITLE1, ZFOLDER FROM ZICCLOUDSYNCINGOBJECT WHERE Z_ENT=? AND COALESCE(ZMARKEDFORDELETION,0)=0",
            (entities["ICNote"],),
        ):
            folder = folders.get(row["ZFOLDER"])
            if folder:
                notes[(folder, normalized_title(row["ZTITLE1"]))].append(int(row["Z_PK"]))
        attachments: dict[int, list[dict]] = defaultdict(list)
        for row in db.execute(
            """
            SELECT Z_PK, ZNOTE, ZTITLE, ZDURATION
            FROM ZICCLOUDSYNCINGOBJECT
            WHERE Z_ENT=?
              AND ZTYPEUTI='com.apple.m4a-audio'
              AND ZPARENTATTACHMENT IS NULL
              AND ZPARENTATTACHMENT1 IS NULL
              AND COALESCE(ZMARKEDFORDELETION,0)=0
            """,
            (entities["ICAttachment"],),
        ):
            if row["ZNOTE"] is None:
                continue
            attachments[int(row["ZNOTE"])].append(
                {
                    "pk": int(row["Z_PK"]),
                    "title": row["ZTITLE"] or "",
                    "normalized_title": normalized_title(row["ZTITLE"]),
                    "seconds": rounded_seconds(row["ZDURATION"] or 0),
                }
            )
    return dict(notes), dict(attachments)


def load_visible(raw: str) -> tuple[int, list[dict]]:
    payload = json.load(sys.stdin) if raw == "-" else json.loads(raw)
    if isinstance(payload, list):
        rows, total = payload, len(payload)
    else:
        rows = payload.get("rows", [])
        total = int(payload.get("total_count", len(rows)))
    visible = []
    for index, row in enumerate(rows):
        title = row.get("title") or row.get("description") or ""
        duration = row.get("duration_seconds", row.get("duration"))
        visible.append(
            {
                "index": index,
                "title": title,
                "normalized_title": normalized_title(title),
                "seconds": parse_duration(duration),
            }
        )
    return total, visible


def base_title(row: dict) -> str:
    started = row["started"]
    return f"{started.month}월 {started.day}일 {row['course']}"


def valid_existing_title(current: str, base: str) -> bool:
    current_norm, base_norm = normalized_title(current), normalized_title(base)
    return current_norm == base_norm or bool(re.fullmatch(re.escape(base_norm) + r" \d+-\d+", current_norm))


def assign_titles(group: list[dict]) -> None:
    count = len(group)
    used: set[int] = set()
    for row in group:
        base = base_title(row)
        if valid_existing_title(row["current_title"], base):
            row["desired_title"] = normalized_title(row["current_title"])
            match = re.search(r" (\d+)-(\d+)$", row["desired_title"])
            if match:
                used.add(int(match.group(1)))
            elif count > 1:
                used.add(1)
        else:
            row["desired_title"] = None
    for chronological_index, row in enumerate(group, 1):
        if row["desired_title"] is not None:
            continue
        base = base_title(row)
        if count == 1:
            row["desired_title"] = base
            continue
        position = chronological_index
        if position in used:
            position = next(number for number in range(1, count + 1) if number not in used)
        used.add(position)
        row["desired_title"] = f"{base} {position}-{count}"


def serialize_row(row: dict) -> dict:
    return {
        "z_pk": row["z_pk"],
        "started_local": row["started"].isoformat(timespec="seconds"),
        "course": row["course"],
        "current_title": row["current_title"],
        "desired_title": row.get("desired_title"),
        "duration_seconds": row["seconds"],
    }


def build_plan(voice_db: Path, notes_db: Path, today: date, visible_raw: str) -> dict:
    total_count, visible = load_visible(visible_raw)
    if total_count != len(visible):
        return {"status": "fatal", "reason": "incomplete_all_recordings_ax", "expected_rows": total_count, "received_rows": len(visible), "actions": []}

    voice_rows, ignored_keys = load_voice_rows(voice_db, today)
    voice_by_key: dict[tuple[str, int], list[dict]] = defaultdict(list)
    voice_by_seconds: dict[int, list[dict]] = defaultdict(list)
    for row in voice_rows:
        voice_by_key[(normalized_title(row["current_title"]), row["seconds"])].append(row)
        voice_by_seconds[row["seconds"]].append(row)
    visible_counts = Counter((row["normalized_title"], row["seconds"]) for row in visible)

    verified, reports = [], []
    for item in visible:
        key = (item["normalized_title"], item["seconds"])
        if key in ignored_keys:
            continue
        matches = voice_by_key.get(key, [])
        if len(matches) == 1 and visible_counts[key] == 1:
            row = dict(matches[0])
            row["visible_title"] = item["title"]
            verified.append(row)
            continue
        duration_matches = voice_by_seconds.get(item["seconds"], [])
        if duration_matches:
            reports.append({"type": "list_unverified", "title": item["title"], "duration_seconds": item["seconds"]})

    if reports:
        return {"status": "fatal", "reason": "voice_memos_identity_mismatch", "actions": [], "reports": reports}

    timetable_rows = []
    for row in verified:
        if row["course"] is None:
            reports.append({"type": "timetable_mismatch", **serialize_row(row)})
        else:
            timetable_rows.append(row)

    grouped: dict[tuple[date, str], list[dict]] = defaultdict(list)
    for row in timetable_rows:
        grouped[(row["started"].date(), row["course"])].append(row)
    for group in grouped.values():
        group.sort(key=lambda row: row["started"])
        assign_titles(group)

    notes, attachments = load_notes(notes_db)
    actions, completed = [], []
    for (lecture_date, course), group in sorted(grouped.items()):
        note_folder = normalized_title(NOTE_FOLDERS[course])
        note_date = f"{lecture_date.month}/{lecture_date.day}"
        note_ids = notes.get((note_folder, normalized_title(note_date)), [])
        if len(note_ids) != 1:
            issue = "note_missing" if not note_ids else "note_duplicate"
            reports.append({"type": issue, "course": course, "date": note_date, "count": len(note_ids)})
            for row in group:
                rename_to = row["desired_title"] if normalized_title(row["current_title"]) != normalized_title(row["desired_title"]) else None
                if rename_to:
                    actions.append({"type": "rename", "rename_to": rename_to, **serialize_row(row)})
            continue

        note_pk = note_ids[0]
        note_attachments = attachments.get(note_pk, [])
        expected_pairs = {(normalized_title(row["desired_title"]), row["seconds"]) for row in group}
        extras = [item for item in note_attachments if (item["normalized_title"], item["seconds"]) not in expected_pairs]
        duplicate_pairs = [pair for pair, count in Counter((item["normalized_title"], item["seconds"]) for item in note_attachments).items() if count > 1]
        note_has_issue = bool(extras or duplicate_pairs)
        if note_has_issue:
            reports.append(
                {
                    "type": "existing_attachment_issue",
                    "course": course,
                    "date": note_date,
                    "note_pk": note_pk,
                    "extras": [{"pk": item["pk"], "title": item["title"], "duration_seconds": item["seconds"]} for item in extras],
                    "duplicates": [{"title": title, "duration_seconds": seconds} for title, seconds in duplicate_pairs],
                }
            )

        for row in group:
            rename_to = row["desired_title"] if normalized_title(row["current_title"]) != normalized_title(row["desired_title"]) else None
            matching = [item for item in note_attachments if item["normalized_title"] == normalized_title(row["desired_title"]) and item["seconds"] == row["seconds"]]
            if rename_to:
                actions.append({"type": "rename", "rename_to": rename_to, **serialize_row(row)})
            if note_has_issue:
                continue
            if len(matching) == 1:
                completed.append(serialize_row(row))
            elif not matching:
                actions.append({"type": "attach", "note_pk": note_pk, "note_folder": NOTE_FOLDERS[course], "note_date": note_date, **serialize_row(row)})
            else:
                reports.append({"type": "existing_attachment_issue", "course": course, "date": note_date, "note_pk": note_pk})

    return {
        "status": "ok",
        "all_recordings_count": total_count,
        "verified_candidates": len(verified),
        "actions": actions,
        "completed": completed,
        "reports": reports,
        "summary": {
            "rename": sum(action["type"] == "rename" for action in actions),
            "attach": sum(action["type"] == "attach" for action in actions),
            "completed": len(completed),
            "reports": len(reports),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--visible-json", required=True, help="JSON array/object from Voice Memos 모든 녹음 항목 AX; use - for stdin")
    parser.add_argument("--voice-db", type=Path, default=VOICE_DB)
    parser.add_argument("--notes-db", type=Path, default=NOTES_DB)
    parser.add_argument("--today", type=date.fromisoformat, default=datetime.now(SEOUL).date())
    args = parser.parse_args()
    try:
        emit(build_plan(args.voice_db, args.notes_db, args.today, args.visible_json))
    except (FileNotFoundError, ValueError, sqlite3.Error, RuntimeError, json.JSONDecodeError) as error:
        emit({"status": "fatal", "reason": type(error).__name__, "detail": str(error), "actions": []}, 2)


if __name__ == "__main__":
    main()
