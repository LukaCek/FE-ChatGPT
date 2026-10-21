"""Evidence-based first-semester course dashboard.

A Moodle upload, an empty notebook, or a timetable entry is not proof that a
lecture occurred. Studying a lecture is not proof of subject mastery.
"""
from __future__ import annotations

from collections import Counter
from datetime import date

COURSES = {
    "01_MAT": "Matematika I",
    "02_FIZ_MiT": "Mehanika in toplota",
    "03_PROG": "Programiranje I",
    "04_ELE": "Osnove elektrotehnike I",
}
CONFIRMED = {"short_delay_confirmed", "week_later_confirmed"}


def build_curriculum_dashboard(tracker, subject: str = "") -> dict:
    """Summarize *recorded* teaching, study completion and observed skill separately.

    The missing-data state is intentionally explicit. Never estimate syllabus
    completion or a percentage of 'knowledge' from a handful of recorded tasks.
    """
    if not isinstance(subject, str) or len(subject) > 140:
        raise ValueError("invalid subject")
    with tracker.db() as conn:
        lessons = [dict(row) for row in conn.execute(
            "SELECT subject, lesson, taught_on, scheduled_for, studied_on, "
            "study_stage, source_kind, evidence FROM lessons"
        )]
        concepts = [dict(row) for row in conn.execute(
            "SELECT id, subject FROM concepts ORDER BY subject_key, topic_key"
        )]
        # Call the established evaluator, which correctly handles voided attempts,
        # failed attempts and delayed recall. Do not infer mastery from a simple
        # count of successful historical attempts.
        knowledge = [tracker._knowledge_in_conn(conn, row["id"])
                     for row in concepts]
    today = tracker._now().date()
    names = dict(COURSES)
    names.update({row["subject"]: COURSES.get(row["subject"], row["subject"])
                  for row in lessons + knowledge})
    if subject:
        names = {subject: names.get(subject, subject)}
    subjects = []
    for code, name in names.items():
        ls = [r for r in lessons if r["subject"].casefold() == code.casefold()]
        ts = [r for r in knowledge if r["subject"].casefold() == code.casefold()]
        confirmed = sorted((r for r in ls if r["taught_on"] and
                            r["taught_on"] <= today.isoformat()),
                           key=lambda r: (r["taught_on"], r["lesson"]))
        complete = sorted((r for r in ls if r["study_stage"] == "completed"),
                          key=lambda r: (r["studied_on"] or "", r["lesson"]))
        incomplete = [r for r in confirmed if r["study_stage"] != "completed"]
        planned = [r for r in ls if r["scheduled_for"] and not r["taught_on"]]
        current = confirmed[-1] if confirmed else None
        last_complete = complete[-1] if complete else None
        stages = dict(Counter(r["study_stage"] for r in ls))
        states = dict(Counter(r["status"] for r in ts))
        subjects.append({
            "subject": code, "name": name,
            "teacher_frontier": ({
                "lesson": current["lesson"],
                "taught_on": current["taught_on"],
                "source_kind": current["source_kind"],
                "evidence": current["evidence"],
                "age_days": (today - date.fromisoformat(current["taught_on"])).days
            } if current else None),
            "student_frontier": ({
                "lesson": last_complete["lesson"],
                "studied_on": last_complete["studied_on"]
            } if last_complete else None),
            "lesson_count_recorded": len(ls),
            "confirmed_taught_count": len(confirmed),
            "started_count": stages.get("started", 0),
            "completed_count": stages.get("completed", 0),
            "confirmed_unfinished_count": len(incomplete),
            "oldest_unfinished_date": min((x["taught_on"] for x in incomplete),
                                          default=None),
            "scheduled_not_confirmed_count": len(planned),
            "knowledge_topic_count": len(ts),
            "knowledge_status_counts": states,
            "independent_or_better_count": sum(r["status"] in
                ("first_independent", "unconfirmed_repeated_practice",
                 "short_delay_confirmed", "week_later_confirmed") for r in ts),
            "delayed_recall_confirmed_count": sum(r["status"] in CONFIRMED for r in ts),
            "missing_teacher_progress": current is None,
            "missing_student_completion_evidence": last_complete is None,
            "coverage_warning": ("No confirmed dated lessons: current teaching "
                "position unknown." if current is None else
                "Only documented lessons are counted; gaps in notebooks are possible."),
        })
    return {
        "as_of": today.isoformat(),
        "courses": subjects,
        "summary": {
            "course_count": len(subjects),
            "courses_with_confirmed_teacher_frontier": sum(
                not x["missing_teacher_progress"] for x in subjects),
            "confirmed_unfinished_lessons": sum(
                x["confirmed_unfinished_count"] for x in subjects),
            "completed_lessons": sum(x["completed_count"] for x in subjects),
        },
        "limits": [
            "Lesson counts are only the verified subset, not the full syllabus.",
            "Scheduled lectures and Moodle uploads do not prove what was taught.",
            "An unfinished recorded lesson is not necessarily an entire missed lecture.",
            "Topic attempts do not establish syllabus coverage or permanent mastery.",
            "Teacher progress and study progress are separate; no gap percentage is estimated.",
        ],
    }
