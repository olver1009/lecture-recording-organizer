from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import plan  # noqa: E402
import wait_for_attachment  # noqa: E402


class PlannerTests(unittest.TestCase):
    def test_normalizes_notes_attachment_titles(self) -> None:
        self.assertEqual(plan.normalized_title(" 9/21  강의.m4a "), "9/21 강의")
        self.assertEqual(plan.normalized_title("9:21 강의"), "9/21 강의")

    def test_classifies_core_and_early_start(self) -> None:
        self.assertEqual(plan.classify(datetime(2026, 9, 21, 9, 0, tzinfo=plan.SEOUL)), "미주지역지리")
        self.assertEqual(plan.classify(datetime(2026, 9, 21, 12, 50, tzinfo=plan.SEOUL)), "지도학및실습")
        self.assertIsNone(plan.classify(datetime(2026, 9, 21, 12, 44, tzinfo=plan.SEOUL)))

    def test_replaces_wrong_or_duplicate_split_slots(self) -> None:
        group = [
            {"started": datetime(2026, 9, 21, 13, 0, tzinfo=plan.SEOUL), "course": "지도학및실습", "current_title": "9월 21일 지도학및실습 1-9"},
            {"started": datetime(2026, 9, 21, 13, 40, tzinfo=plan.SEOUL), "course": "지도학및실습", "current_title": "9월 21일 지도학및실습 1-2"},
        ]
        plan.assign_titles(group)
        self.assertEqual(group[0]["desired_title"], "9월 21일 지도학및실습 2-2")
        self.assertEqual(group[1]["desired_title"], "9월 21일 지도학및실습 1-2")

        duplicate_group = [
            {"started": datetime(2026, 9, 21, 13, 0, tzinfo=plan.SEOUL), "course": "지도학및실습", "current_title": "9월 21일 지도학및실습 1-2"},
            {"started": datetime(2026, 9, 21, 13, 40, tzinfo=plan.SEOUL), "course": "지도학및실습", "current_title": "9월 21일 지도학및실습 1-2"},
        ]
        plan.assign_titles(duplicate_group)
        self.assertEqual(duplicate_group[0]["desired_title"], "9월 21일 지도학및실습 1-2")
        self.assertEqual(duplicate_group[1]["desired_title"], "9월 21일 지도학및실습 2-2")

    def test_attachment_checker_complete_duplicate_and_pending(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "NoteStore.sqlite"
            db = sqlite3.connect(db_path)
            db.executescript(
                """
                CREATE TABLE Z_PRIMARYKEY (Z_ENT INTEGER, Z_NAME TEXT);
                CREATE TABLE ZICCLOUDSYNCINGOBJECT (
                    Z_PK INTEGER, Z_ENT INTEGER, ZTITLE2 TEXT, ZTITLE1 TEXT,
                    ZFOLDER INTEGER, ZMARKEDFORDELETION INTEGER, ZNOTE INTEGER,
                    ZTITLE TEXT, ZDURATION REAL, ZTYPEUTI TEXT,
                    ZPARENTATTACHMENT INTEGER, ZPARENTATTACHMENT1 INTEGER
                );
                INSERT INTO Z_PRIMARYKEY VALUES (1, 'ICAttachment'), (2, 'ICNote'), (3, 'ICFolder');
                INSERT INTO ZICCLOUDSYNCINGOBJECT
                    (Z_PK, Z_ENT, ZNOTE, ZTITLE, ZDURATION, ZTYPEUTI, ZMARKEDFORDELETION)
                    VALUES (10, 1, 99, '9월 21일 도시지리학특강.m4a', 4462.1, 'com.apple.m4a-audio', 0);
                """
            )
            db.commit()
            db.close()

            result = wait_for_attachment.check_attachment(db_path, 99, "9월 21일 도시지리학특강", 4462)
            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["attachment_pk"], 10)

            db = sqlite3.connect(db_path)
            db.execute(
                """INSERT INTO ZICCLOUDSYNCINGOBJECT
                (Z_PK, Z_ENT, ZNOTE, ZTITLE, ZDURATION, ZTYPEUTI, ZMARKEDFORDELETION)
                VALUES (11, 1, 99, ?, 4462.1, 'com.apple.m4a-audio', 0)""",
                ("9월 21일 도시지리학특강.m4a",),
            )
            db.commit()
            db.close()
            self.assertEqual(
                wait_for_attachment.check_attachment(db_path, 99, "9월 21일 도시지리학특강", 4462)["status"],
                "duplicate",
            )
            self.assertEqual(wait_for_attachment.check_attachment(db_path, 100, "없음", 1)["status"], "pending")
            self.assertEqual(
                wait_for_attachment.wait_for_attachment(db_path, 100, "없음", 1, timeout=0, interval=0.05)["status"],
                "timeout",
            )


if __name__ == "__main__":
    unittest.main()
