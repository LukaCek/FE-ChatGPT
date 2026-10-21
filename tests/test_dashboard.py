"""Evidence-only curriculum dashboard: no fabricated course coverage."""
from datetime import datetime, timedelta, timezone

from fe_gradiva.study import StudyTracker
from fe_gradiva.dashboard import build_curriculum_dashboard


def make_tracker(tmp_path):
    now = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
    return StudyTracker(tmp_path / "study.sqlite", clock=lambda: now)


def by_code(dashboard, code):
    return next(c for c in dashboard["courses"] if c["subject"] == code)


def test_empty_tracker_keeps_all_first_semester_courses_unknown(tmp_path):
    t = make_tracker(tmp_path)
    data = build_curriculum_dashboard(t)
    assert data["summary"]["course_count"] == 4
    assert data["summary"]["confirmed_unfinished_lessons"] == 0
    assert data["summary"]["courses_with_confirmed_teacher_frontier"] == 0
    for c in data["courses"]:
        assert c["teacher_frontier"] is None
        assert c["student_frontier"] is None
        assert c["missing_teacher_progress"] is True
        assert c["knowledge_topic_count"] == 0


def test_latest_teaching_and_last_studied_are_separate(tmp_path):
    t = make_tracker(tmp_path)
    t.record_lesson(
        "01_MAT", "Limite", "completed", taught_on="2026-10-13",
        source_kind="verified_class_notes",
        evidence="Datiran zapis v zvezku: 13.10.2026, limite."
    )
    t.record_lesson(
        "01_MAT", "Odvodi", "started", taught_on="2026-10-20",
        source_kind="verified_class_notes",
        evidence="Datiran zapis v zvezku: 20.10.2026, odvodi."
    )
    c = by_code(build_curriculum_dashboard(t), "01_MAT")
    assert c["teacher_frontier"]["lesson"] == "Odvodi"
    assert c["teacher_frontier"]["taught_on"] == "2026-10-20"
    assert c["student_frontier"]["lesson"] == "Limite"
    assert c["confirmed_unfinished_count"] == 1
    assert c["completed_count"] == 1
    assert c["knowledge_topic_count"] == 0


def test_timetable_is_never_treated_as_taught_or_backlog(tmp_path):
    t = make_tracker(tmp_path)
    t.record_lesson(
        "03_PROG", "Funkcije", "not_started", scheduled_for="2026-10-20",
        source_kind="official_timetable", evidence="Na urniku je bila načrtovana ura."
    )
    c = by_code(build_curriculum_dashboard(t), "03_PROG")
    assert c["teacher_frontier"] is None
    assert c["confirmed_unfinished_count"] == 0
    assert c["scheduled_not_confirmed_count"] == 1


def test_practice_success_not_long_term_confirmation(tmp_path):
    t = make_tracker(tmp_path)
    t.record_attempt(
        "04_ELE", "Električno polje", "independent",
        evidence="Samostojno izračunal velikost polja.",
        assessment="practice", exercise_ref="Zaključno preverjanje 2"
    )
    c = by_code(build_curriculum_dashboard(t), "04_ELE")
    assert c["knowledge_topic_count"] == 1
    assert c["knowledge_status_counts"] == {"first_independent": 1}
    assert c["independent_or_better_count"] == 1
    assert c["delayed_recall_confirmed_count"] == 0
    assert c["missing_teacher_progress"] is True


def test_completed_without_taught_date_is_not_a_confirmed_lecture(tmp_path):
    t = make_tracker(tmp_path)
    t.record_lesson(
        "02_FIZ_MiT", "Ponovitev vektorjev", "completed",
        evidence="Samostojno rešene vaje, brez datuma predavanja."
    )
    c = by_code(build_curriculum_dashboard(t), "02_FIZ_MiT")
    assert c["student_frontier"]["lesson"] == "Ponovitev vektorjev"
    assert c["teacher_frontier"] is None
    assert c["confirmed_taught_count"] == 0


def test_dashboard_subject_filter_and_unknown_subject(tmp_path):
    t = make_tracker(tmp_path)
    filtered = build_curriculum_dashboard(t, "03_PROG")
    assert len(filtered["courses"]) == 1
    assert filtered["courses"][0]["name"] == "Programiranje I"
    unknown = build_curriculum_dashboard(t, "05_X")
    assert unknown["courses"][0]["teacher_frontier"] is None
