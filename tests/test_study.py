"""FE Gradiva 1.2: lasting memory must never be inferred from one solution."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from fe_gradiva.study import StudyTracker

UTC = timezone.utc


class Clock:
    def __init__(self):
        self.value = datetime(2026,10,19,12,0,tzinfo=UTC)
    def __call__(self):
        return self.value
    def forward(self,days):
        self.value += timedelta(days=days)


def tracker(tmp_path):
    clock=Clock()
    db=tmp_path/'tracker.sqlite'
    return StudyTracker(db,clock=clock),clock,db


def log(t,topic="Coulombov zakon",outcome="independent",assessment="practice",ref="naloga 1",**kwargs):
    return t.record_attempt("Osnove elektrotehnike I",topic,outcome,
                            evidence="Samostojno izračunal silo in pojasnil smer." if outcome=="independent" else
                            "Potreboval namig glede predznaka.",
                            assessment=assessment,exercise_ref=ref,**kwargs)


def test_one_independent_attempt_is_provisional_not_memorized(tmp_path):
    t,clock,_=tracker(tmp_path)
    a=log(t)
    k=a['knowledge']
    assert a['recorded'] is True
    assert k['status']=="first_independent"
    assert k['independent_attempts']==1
    assert k['different_task_recall_after_7d'] is False
    assert "ne potrjeno pomnjenje" in k['interpretation']
    assert not k['review_due']
    clock.forward(3)
    overdue=t.knowledge()["topics"][0]
    assert overdue["review_due"] is True
    assert overdue["days_since_last_attempt"]==3.0
    assert overdue["status"]=="first_independent"  # never upgrades with time alone


def test_repeat_same_exercise_week_later_is_not_retained(tmp_path):
    t,clock,_=tracker(tmp_path)
    log(t)
    clock.forward(8)
    k=log(t,assessment="delayed_recall",ref="naloga 1")["knowledge"]
    assert k["different_task_recall_after_7d"] is False
    assert k["status"]=="same_period_practice"
    assert k["review_due"] is False


def test_different_independent_delayed_exercise_week_later_is_supported(tmp_path):
    t,clock,_=tracker(tmp_path)
    log(t,ref="A")
    clock.forward(2)
    early=log(t,ref="B",assessment="delayed_recall")["knowledge"]
    assert early["status"]=="short_delay_confirmed"
    assert not early["different_task_recall_after_7d"]
    clock.forward(8)
    late=log(t,ref="C",assessment="delayed_recall")["knowledge"]
    assert late["status"]=="week_later_confirmed"
    assert late["different_task_recall_after_7d"] is True
    assert "ne trajno znanje" in late["interpretation"]
    assert late["attempts"]==3


def test_incorrect_or_guided_response_downgrades_previous_success(tmp_path):
    t,clock,_=tracker(tmp_path)
    log(t,ref="A")
    clock.forward(8)
    log(t,ref="B",assessment="delayed_recall")
    k=log(t,outcome="guided",ref="C")["knowledge"]
    assert k["status"]=="needs_independent_test"
    assert k["review_due"] is True
    assert len(t.reviews()["due"])==1
    k=log(t,outcome="incorrect",ref="D",difficulties=["smer vektorja","predznak"])["knowledge"]
    assert k["status"]=="needs_practice"
    assert k["common_difficulties"][0]["name"]=="smer vektorja"


def test_self_report_does_not_prove_recall(tmp_path):
    t,clock,_=tracker(tmp_path)
    with pytest.raises(ValueError,match="self_report"):
        log(t,outcome="independent",assessment="self_report")
    state=log(t,outcome="guided",assessment="self_report")["knowledge"]
    assert state["independent_attempts"]==0
    assert state["status"]=="self_report_only"
    assert state["review_due"]


def test_guided_solution_never_counts_as_independent(tmp_path):
    t,clock,_=tracker(tmp_path)
    result=log(t,outcome="guided",ref="naloga A")['knowledge']
    assert result["independent_attempts"]==0
    assert result["last_outcome"]=="guided"
    assert "pomoč" in result["interpretation"]
    assert result["review_due"]


def test_retry_idempotence_and_non_destructive_correction(tmp_path):
    t,clock,db=tracker(tmp_path)
    one=log(t,attempt_id="conversationA-exercise1")
    duplicate=log(t,attempt_id="conversationA-exercise1")
    assert one["recorded"] is True
    assert duplicate["recorded"] is False and duplicate["duplicate"] is True
    with pytest.raises(ValueError,match="another concept"):
        log(t,topic="Gaussov zakon",attempt_id="conversationA-exercise1")
    fixed=t.void_attempt(one["attempt_id"],"Spregledal sem pomoč in naknadno prepisano rešitev.")
    assert fixed["knowledge"]["attempts"]==0
    history=t.history()
    assert history["count"]==1
    assert history["attempts"][0]["void_reason"]
    assert history["attempts"][0]["voided_at"]
    again=StudyTracker(db,clock=clock)
    assert again.knowledge()["topics"][0]["attempts"]==0


def test_attempt_metadata_guardrails(tmp_path):
    t,clock,_=tracker(tmp_path)
    for subject in (""," "*3,"a"*145):
        with pytest.raises(ValueError):
            t.record_attempt(subject,"Tema","independent","neki rezultat")
    with pytest.raises(ValueError):
        log(t,difficulties=["x"]*9)
    with pytest.raises(ValueError):
        log(t,outcome="unknown")
    with pytest.raises(ValueError):
        log(t,assessment="bogus")


def test_lecture_schedule_alone_cannot_create_backlog(tmp_path):
    t,clock,_=tracker(tmp_path)
    t.record_lesson("Matematika I","Limite","not_started",
                    scheduled_for="2026-10-10",source_kind="official_timetable",
                    evidence="Urnik predavanj navaja 10. oktober.")
    progress=t.course_progress("Matematika I")
    assert progress["behind_count"]==0
    assert len(progress["scheduled_unconfirmed"])==1
    with pytest.raises(ValueError,match="cannot confirm"):
        t.record_lesson("Matematika I","Limite","not_started",
                        taught_on="2026-10-10",source_kind="official_timetable",
                        evidence="Urnik")
    with pytest.raises(ValueError,match="require verified"):
        t.record_lesson("Matematika I","Odvodi","not_started",
                        taught_on="2026-10-11")
    assert t.course_progress()["behind_count"]==0


def test_verified_lectures_count_as_backlog_until_studied(tmp_path):
    t,clock,_=tracker(tmp_path)
    t.record_lesson("Matematika I","Integrali","not_started",
                    taught_on="2026-10-13",source_kind="user_confirmed",
                    evidence="Uporabnik je potrdil izvedeno predavanje.")
    progress=t.course_progress()
    assert progress["behind_count"]==1
    assert progress["behind"][0]["days_behind"]==6
    plan=t.study_plan("Matematika I",minutes=30)
    assert plan["tasks"][0]["kind"]=="catch_up"
    t.record_lesson("Matematika I","Integrali","completed",
                    evidence="Uporabnik je rešil vaje.")
    assert t.course_progress()["studied"]==1
    assert t.course_progress()["behind_count"]==0
    assert t.knowledge()["count"]==0  # studying a lecture != mastering its concepts


def test_study_plan_prefers_weakness_over_lecture_backlog(tmp_path):
    t,clock,_=tracker(tmp_path)
    log(t,topic="Električno polje",outcome="incorrect",
        difficulties=["vektorji"])
    t.record_lesson("Matematika I","Limite","not_started",
                    taught_on="2026-10-10",source_kind="user_confirmed",
                    evidence="Izvedena ura potrjena.")
    plan=t.study_plan(minutes=60)
    assert plan["tasks"][0]["kind"]=="review"
    assert plan["tasks"][0]["topic"]=="Električno polje"
    assert any(x["kind"]=="catch_up" for x in plan["tasks"])
    assert plan["allocated_minutes"]<=60


def test_storage_separate_from_readonly_materials(tmp_path):
    root=tmp_path/"FE";root.mkdir()
    materials=root/"gradivo.md"
    materials.write_text("Profesorjeva snov",encoding="utf-8")
    database=tmp_path/"persist"/"study.sqlite"
    t=StudyTracker(database)
    t.record_attempt("Matematika I","Limite","guided","Potreboval usmeritev.")
    assert database.exists()
    assert list(root.iterdir())==[materials]
    assert materials.read_text()=="Profesorjeva snov"


def test_multiple_topics_and_subjects_appear_in_filtered_history(tmp_path):
    t,clock,_=tracker(tmp_path)
    log(t)
    t.record_attempt("Programiranje I","Zanke","guided","Napaka pri meji zanke.")
    assert t.knowledge()["count"]==2
    assert t.knowledge("Programiranje I")["count"]==1
    assert t.history("Programiranje I")["count"]==1
    assert t.reviews("Programiranje I")["count"]==1
