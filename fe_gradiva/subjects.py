"""Canonical FE course IDs and audited migration for legacy subject aliases."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import unicodedata


def norm(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


COURSE_ALIASES = {
    "01_MAT": ("01_MAT", "Matematika I", "Matematika 1"),
    "02_FIZ_MiT": ("02_FIZ_MiT", "Fizika I", "Mehanika in toplota"),
    "03_PROG": ("03_PROG", "Programiranje I", "Programiranje 1"),
    "04_ELE": ("04_ELE", "Elektrotehnika I", "Elektrotehnika 1",
               "Osnove elektrotehnike I", "Osnove elektrotehnike 1"),
}
LOOKUP = {norm(alias): code for code, aliases in COURSE_ALIASES.items()
          for alias in aliases}


def canonical_subject(value: str) -> str:
    """Only explicit aliases are merged; different courses remain distinct."""
    return LOOKUP.get(norm(value), value)


def subject_key(value: str) -> str:
    return norm(canonical_subject(value))


def migrate_subjects(conn) -> None:
    """Idempotent, auditable merge; caller owns the SQLite transaction."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS subject_alias_migration_archive (
          table_name TEXT NOT NULL,
          row_id TEXT NOT NULL,
          original_json TEXT NOT NULL,
          archived_at TEXT NOT NULL,
          PRIMARY KEY (table_name, row_id)
        )
    """)
    now = datetime.now(timezone.utc).isoformat()

    def archive(table, row):
        conn.execute(
            """INSERT OR IGNORE INTO subject_alias_migration_archive
               (table_name,row_id,original_json,archived_at) VALUES (?,?,?,?)""",
            (table, str(row["id"]), json.dumps(dict(row), ensure_ascii=False), now))

    for table, qualifier in (("concepts", "topic_key"),
                             ("lessons", "lesson_key"),
                             ("exam_assessments", "name_key")):
        ids = conn.execute(f"SELECT id FROM {table} ORDER BY id").fetchall()
        for original in ids:
            row = conn.execute(f"SELECT * FROM {table} WHERE id=?",
                               (original["id"],)).fetchone()
            if row is None:
                continue
            code = canonical_subject(row["subject"])
            key = subject_key(code)
            if row["subject"] == code and row["subject_key"] == key:
                continue
            duplicate = conn.execute(
                f"SELECT * FROM {table} WHERE subject_key=? AND {qualifier}=? AND id<>?",
                (key, row[qualifier], row["id"])).fetchone()
            archive(table, row)
            if duplicate is None:
                conn.execute(f"UPDATE {table} SET subject=?,subject_key=? WHERE id=?",
                             (code, key, row["id"]))
                continue
            archive(table, duplicate)
            if table == "concepts":
                conn.execute("UPDATE attempts SET concept_id=? WHERE concept_id=?",
                             (duplicate["id"], row["id"]))
            elif table == "lessons":
                stages = {"not_started": 0, "started": 1, "completed": 2}
                stage = max((row["study_stage"], duplicate["study_stage"]),
                            key=lambda value: stages[value])
                conn.execute("""
                    UPDATE lessons SET scheduled_for=?, taught_on=?, studied_on=?,
                      study_stage=?, source_kind=?, evidence=?, updated_at=?
                    WHERE id=?""",
                    (duplicate["scheduled_for"] or row["scheduled_for"],
                     duplicate["taught_on"] or row["taught_on"],
                     duplicate["studied_on"] or row["studied_on"],
                     stage, duplicate["source_kind"] or row["source_kind"],
                     duplicate["evidence"] or row["evidence"],
                     max(duplicate["updated_at"], row["updated_at"]),
                     duplicate["id"]))
            else:
                topics = json.loads(duplicate["topics_json"])
                seen = {norm(t) for t in topics}
                for topic in json.loads(row["topics_json"]):
                    if norm(topic) not in seen:
                        seen.add(norm(topic))
                        topics.append(topic)
                conn.execute("""
                    UPDATE exam_assessments SET topics_json=?, scheduled_for=?,
                      source_kind=?, evidence=?, updated_at=?
                    WHERE id=?""",
                    (json.dumps(topics, ensure_ascii=False),
                     duplicate["scheduled_for"] or row["scheduled_for"],
                     duplicate["source_kind"] or row["source_kind"],
                     duplicate["evidence"] or row["evidence"],
                     max(duplicate["updated_at"], row["updated_at"]),
                     duplicate["id"]))
            conn.execute(f"DELETE FROM {table} WHERE id=?", (row["id"],))
