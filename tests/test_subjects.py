"""Regressions for real FE Gradiva course-name inconsistencies."""
import json
import sqlite3

from fe_gradiva.dashboard import build_curriculum_dashboard
from fe_gradiva.study import StudyTracker


def test_subject_aliases_unify_attempts_history_reviews_and_plan(tmp_path):
    t = StudyTracker(tmp_path / "study.db")
    result = t.record_attempt(
        "Matematika I", "Vektorji", "incorrect", "Napačna komponenta",
        exercise_ref="Vaje str. 4, naloga 1", attempt_id="alias-1")
    assert result["recorded"]
    assert t.knowledge("01_MAT")["count"] == 1
    assert t.knowledge("Matematika I")["count"] == 1
    assert t.history("Matematika I")["count"] == 1
    assert t.reviews("Matematika I")["count"] == 1
    assert t.study_plan("Matematika I", 20)["tasks"][0]["kind"] == "review"
    retry = t.record_attempt(
        "01_MAT", "Vektorji", "incorrect", "Napačna komponenta",
        exercise_ref="Vaje str. 4, naloga 1", attempt_id="alias-1")
    assert retry["duplicate"] and not retry["recorded"]


def test_different_courses_never_merge(tmp_path):
    t = StudyTracker(tmp_path / "study.db")
    t.record_attempt("Programiranje II", "For", "independent", "Sam rešil.")
    assert t.knowledge("03_PROG")["count"] == 0
    assert t.knowledge("Programiranje II")["count"] == 1


def test_legacy_alias_migration_merges_attempts_with_audit_and_voids(tmp_path):
    db = tmp_path / "study.db"
    t = StudyTracker(db)
    first = t.record_attempt("01_MAT", "Limite", "incorrect", "Prvi poskus",
                             attempt_id="legacy-code")
    t.void_attempt(first["attempt_id"], "Napačno ocenjen odgovor")
    with sqlite3.connect(db) as conn:
        conn.execute("""INSERT INTO concepts
          (subject,topic,subject_key,topic_key,created_at)
          VALUES ('Matematika I','Limite','matematika i','limite','2026-10-20')""")
        cid = conn.execute("SELECT id FROM concepts WHERE subject_key='matematika i'").fetchone()[0]
        conn.execute("""INSERT INTO attempts
          (id,concept_id,observed_at,outcome,assessment,exercise_ref,evidence,difficulties,source_path)
          VALUES (?,?,?,'independent','practice','naloga B','Drug poskus','[]','')""",
                     ("legacy-name",cid,"2026-10-21T08:00:00+00:00"))
    t = StudyTracker(db)
    assert t.knowledge("Matematika I")["count"] == 1
    assert t.knowledge("01_MAT")["topics"][0]["attempts"] == 1
    assert t.history("01_MAT")["count"] == 2
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM attempt_voids").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM subject_alias_migration_archive").fetchone()[0] >= 1
    assert StudyTracker(db).history("Matematika I")["count"] == 2


def test_lesson_and_exam_aliases_canonical_and_dashboard_deduplicated(tmp_path):
    t = StudyTracker(tmp_path / "study.db")
    t.record_lesson("Matematika I","Limite","started", evidence="Rešena naloga.")
    t.record_lesson("01_MAT","Odvodi","started", evidence="Pogledan video.")
    assert t.course_progress("Matematika I")["lessons"] == 2
    assert t.course_progress("01_MAT")["lessons"] == 2
    assert t.study_plan("Matematika I",30)["tasks"] == []
    t.set_exam_assessment("Matematika I","1. kolokvij","kolokvij",
                          topics=["Limite"],source_kind="user_confirmed",
                          evidence="Obseg potrdil študent.")
    assert t.exam_readiness("01_MAT")["count"] == 1
    assert t.exam_readiness("Matematika I")["count"] == 1
    dashboard = build_curriculum_dashboard(t)
    assert dashboard["summary"]["course_count"] == 4
    assert len(build_curriculum_dashboard(t,"Matematika I")["courses"]) == 1


def test_lesson_alias_collision_preserves_completion_and_original(tmp_path):
    db = tmp_path / "study.db"
    t = StudyTracker(db)
    t.record_lesson("01_MAT", "Limite", "started", evidence="Prva vaja")
    with sqlite3.connect(db) as conn:
        conn.execute("""INSERT INTO lessons(subject,subject_key,lesson,lesson_key,
            studied_on,study_stage,evidence,updated_at)
            VALUES ('Matematika I','matematika i','Limite','limite',
                    '2026-10-25','completed','Rešene naloge','2026-10-25')""")
    t = StudyTracker(db)
    assert t.course_progress("Matematika I")["lessons"] == 1
    assert t.course_progress("01_MAT")["studied"] == 1
    with sqlite3.connect(db) as conn:
        original = [json.loads(r[0]) for r in conn.execute(
            "SELECT original_json FROM subject_alias_migration_archive")]
        assert any(x.get("evidence") == "Rešene naloge" for x in original)


def test_exam_scope_legacy_collision_preserves_both_scopes(tmp_path):
    db = tmp_path / "study.db"
    t = StudyTracker(db)
    t.set_exam_assessment("01_MAT","1. kolokvij","kolokvij",
                          topics=["Limite"],source_kind="user_confirmed",
                          evidence="Potrjeno.")
    with sqlite3.connect(db) as conn:
        conn.execute("""INSERT INTO exam_assessments(
          id,subject,subject_key,name,name_key,kind,topics_json,
          source_kind,evidence,created_at,updated_at)
          VALUES ('alias-exam','Matematika I','matematika i','1. kolokvij',
            '1. kolokvij','kolokvij','["Odvodi"]','user_confirmed',
            'Potrjeno.','2026-10-20','2026-10-20')""")
    t = StudyTracker(db)
    report = t.exam_readiness("Matematika I")
    assert report["count"] == 1
    assert {r["topic"] for r in report["assessments"][0]["topics"]} == {"Limite","Odvodi"}
