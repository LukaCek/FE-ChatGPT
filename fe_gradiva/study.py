"""FE Gradiva 1.2: evidence-based learning history, separate from read-only FE files.

Study attempts are observations, *never* proofs of long-term mastery. Review
intervals are conservative heuristics, not probabilities of remembering.
Only an independent response to a different task after >=7 days can earn a
'week_later_confirmed' signal. This still does not mean permanent mastery.
"""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import json
import re
import sqlite3
import unicodedata
from uuid import uuid4

OUTCOMES = {"incorrect", "guided", "independent_with_difficulty", "independent"}
ASSESSMENTS = {"practice", "delayed_recall", "self_report"}
LESSON_SOURCES = {"user_confirmed", "official_timetable", "verified_class_notes"}
STUDY_STAGES = {"not_started", "started", "completed"}
OUTCOME_LABELS = {
    "incorrect": "Ni uspelo samostojno",
    "guided": "Uspelo z namigi/pomočjo",
    "independent_with_difficulty": "Samostojno, a s težavami",
    "independent": "Samostojno brez pomembnih namigov",
}
SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS concepts (
    id INTEGER PRIMARY KEY,
    subject TEXT NOT NULL,
    topic TEXT NOT NULL,
    subject_key TEXT NOT NULL,
    topic_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(subject_key, topic_key)
);
CREATE TABLE IF NOT EXISTS attempts (
    id TEXT PRIMARY KEY,
    concept_id INTEGER NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
    observed_at TEXT NOT NULL,
    outcome TEXT NOT NULL,
    assessment TEXT NOT NULL,
    exercise_ref TEXT NOT NULL,
    evidence TEXT NOT NULL,
    difficulties TEXT NOT NULL DEFAULT '[]',
    source_path TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS attempts_concept_date
    ON attempts(concept_id, observed_at DESC, id);
CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY,
    subject TEXT NOT NULL,
    subject_key TEXT NOT NULL,
    lesson TEXT NOT NULL,
    lesson_key TEXT NOT NULL,
    scheduled_for TEXT,
    taught_on TEXT,
    studied_on TEXT,
    study_stage TEXT NOT NULL DEFAULT 'not_started',
    source_kind TEXT NOT NULL DEFAULT '',
    evidence TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    UNIQUE(subject_key, lesson_key)
);
CREATE INDEX IF NOT EXISTS lessons_subject ON lessons(subject_key);
"""


def _key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _text(value: str, name: str, size: int, required: bool = True) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name}: expected text")
    value = " ".join(value.split())
    if (required and not value) or len(value) > size or any(ord(c) < 32 for c in value):
        raise ValueError(f"{name}: missing or too long")
    return value


def _date(value: str, name: str) -> str:
    if not value:
        return ""
    value = _text(value, name, 10)
    try:
        d = date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{name}: date must be YYYY-MM-DD") from None
    if d.isoformat() != value:
        raise ValueError(f"{name}: date must be YYYY-MM-DD")
    return value


def _utc(now: datetime) -> datetime:
    if now.tzinfo is None:
        raise ValueError("server time needs timezone")
    return now.astimezone(timezone.utc)


def _success(row: dict) -> bool:
    return row["assessment"] != "self_report" and row["outcome"] in {
        "independent", "independent_with_difficulty"
    }


def _days(a: datetime, b: datetime) -> float:
    return (a - b).total_seconds() / 86400


class StudyTracker:
    def __init__(self, db_path: str | Path, clock=None):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        with self.db() as conn:
            conn.executescript(SCHEMA)
            conn.execute("PRAGMA journal_mode=WAL")

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=15000")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _now(self) -> datetime:
        return _utc(self.clock())

    @staticmethod
    def _rows(conn, subject: str = ""):
        q = """SELECT c.id, c.subject, c.topic FROM concepts c"""
        args = ()
        if subject:
            q += " WHERE c.subject_key=?"
            args = (_key(subject),)
        q += " ORDER BY c.subject_key,c.topic_key"
        return conn.execute(q, args).fetchall()

    def record_attempt(self, subject: str, topic: str, outcome: str,
                       evidence: str, assessment: str = "practice",
                       exercise_ref: str = "", difficulties: list[str] | None = None,
                       source_path: str = "", attempt_id: str = "") -> dict:
        subject = _text(subject, "subject", 140)
        topic = _text(topic, "topic", 180)
        outcome = _text(outcome, "outcome", 35)
        assessment = _text(assessment, "assessment", 35)
        if outcome not in OUTCOMES or assessment not in ASSESSMENTS:
            raise ValueError("invalid outcome or assessment")
        evidence = _text(evidence, "evidence", 700)
        # A self-report, a correct answer after seeing the solution, or a
        # hint-led exercise must NEVER be recorded as independent performance.
        if assessment == "self_report" and outcome == "independent":
            raise ValueError("self_report cannot claim observed independence")
        exercise_ref = _text(exercise_ref, "exercise_ref", 200, False)
        source_path = _text(source_path, "source_path", 400, False)
        attempt_id = _text(attempt_id, "attempt_id", 100, False) or str(uuid4())
        if not isinstance(difficulties, list) or len(difficulties) > 8 if difficulties is not None else False:
            raise ValueError("difficulties must be a short list")
        details = [_text(x, "difficulty", 100) for x in (difficulties or [])]
        if "not solved" in _key(evidence) and outcome == "independent":
            raise ValueError("contradictory evidence")
        instant = self._now().isoformat()
        with self.db() as conn:
            prior = conn.execute("SELECT id,concept_id FROM attempts WHERE id=?", (attempt_id,)).fetchone()
            if prior:
                data = conn.execute("SELECT c.subject,c.topic FROM concepts c WHERE c.id=?",
                                    (prior["concept_id"],)).fetchone()
                if (_key(data["subject"]),_key(data["topic"])) != (_key(subject),_key(topic)):
                    raise ValueError("attempt_id already used for another concept")
                return {"recorded": False, "duplicate": True, "attempt_id": attempt_id,
                        "knowledge": self._knowledge_in_conn(conn,prior["concept_id"])}
            conn.execute("""INSERT OR IGNORE INTO concepts(subject,topic,subject_key,topic_key,created_at)
                            VALUES(?,?,?,?,?)""",
                         (subject,topic,_key(subject),_key(topic),instant))
            concept = conn.execute("SELECT id FROM concepts WHERE subject_key=? AND topic_key=?",
                                   (_key(subject),_key(topic))).fetchone()["id"]
            conn.execute("""INSERT INTO attempts(id,concept_id,observed_at,outcome,assessment,
                            exercise_ref,evidence,difficulties,source_path)
                            VALUES(?,?,?,?,?,?,?,?,?)""",
                         (attempt_id,concept,instant,outcome,assessment,exercise_ref,
                          evidence,json.dumps(details,ensure_ascii=False),source_path))
            state = self._knowledge_in_conn(conn,concept)
            return {"recorded":True,"duplicate":False,"attempt_id":attempt_id,
                    "knowledge":state}

    def _knowledge_in_conn(self,conn,concept_id:int, now:datetime|None=None) -> dict:
        now=now or self._now()
        concept=conn.execute("SELECT subject,topic FROM concepts WHERE id=?",(concept_id,)).fetchone()
        attempts=[dict(r) for r in conn.execute(
            "SELECT * FROM attempts WHERE concept_id=? ORDER BY observed_at,id",(concept_id,))]
        successes=[r for r in attempts if _success(r)]
        latest=attempts[-1] if attempts else None
        counters=Counter(d for row in attempts for d in json.loads(row["difficulties"]))
        last_date=datetime.fromisoformat(latest["observed_at"]) if latest else None
        since=round(_days(now,last_date),1) if last_date else None
        independent_successes=len(successes)
        spaced_1d=False
        spaced_7d=False
        for i,earlier in enumerate(successes):
            if not earlier["exercise_ref"]:
                continue
            older_time=datetime.fromisoformat(earlier["observed_at"])
            for later in successes[i+1:]:
                # Different actual exercises must be demonstrated; simply
                # repeating a problem after seeing the answer is not recall.
                if (later["exercise_ref"] and later["exercise_ref"] != earlier["exercise_ref"]
                    and later["assessment"] == "delayed_recall"):
                    gap=_days(datetime.fromisoformat(later["observed_at"]),older_time)
                    spaced_1d|=gap>=1
                    spaced_7d|=gap>=7

        if not latest:
            status="unassessed"
            interval=0
            label="Ni preverjeno"
        elif latest["assessment"]=="self_report":
            status="self_report_only"
            interval=1
            label="Samo samoocena – še ni preverjeno"
        elif latest["outcome"]=="incorrect":
            status="needs_practice"
            interval=1
            label="Potrebna ponovna vaja"
        elif latest["outcome"]=="guided":
            status="needs_independent_test"
            interval=1
            label="Uspelo s pomočjo – potreben samostojen preizkus"
        elif spaced_7d and _success(latest):
            status="week_later_confirmed"
            interval=14 if latest["outcome"]=="independent" else 7
            label="Uspešen ponovni preizkus po vsaj 7 dneh (ne trajno znanje)"
        elif spaced_1d and _success(latest):
            status="short_delay_confirmed"
            interval=4
            label="Samostojno potrjeno po vsaj 1 dnevu; še ni tedensko preverjeno"
        elif independent_successes > 1 and _success(latest):
            status="same_period_practice"
            interval=2
            label="Več samostojnih rešitev, brez dokaza trajnega pomnjenja"
        elif _success(latest):
            status="first_independent"
            interval=1 if latest["outcome"]=="independent_with_difficulty" else 2
            label="En samostojen uspeh – razumevanje, ne potrjeno pomnjenje"
        else:
            status="unassessed"
            interval=1
            label="Znanje še ni zanesljivo preverjeno"
        last_success=successes[-1]["observed_at"] if successes else None
        due_at=last_date + timedelta(days=interval) if last_date else None
        due=bool(due_at and now>=due_at)
        if not latest or latest["outcome"] in {"incorrect","guided"} or latest["assessment"]=="self_report":
            due=True
        return {
            "subject":concept["subject"],"topic":concept["topic"],
            "status":status,"interpretation":label,
            "attempts":len(attempts),"independent_attempts":independent_successes,
            "different_task_recall_after_1d":spaced_1d,
            "different_task_recall_after_7d":spaced_7d,
            "last_attempt_at":latest["observed_at"] if latest else None,
            "days_since_last_attempt":since,
            "last_independent_at":last_success,
            "last_outcome":latest["outcome"] if latest else None,
            "last_evidence":latest["evidence"] if latest else None,
            "common_difficulties":[{"name":x,"count":n} for x,n in counters.most_common(5)],
            "recommended_review_at":due_at.isoformat() if due_at else None,
            "review_due":due,
            "caveat":"A single correct solution never proves lasting memory. Re-test independently using a different exercise days later."
        }

    def knowledge(self,subject: str="",topic: str="",limit:int=100)->dict:
        subject=_text(subject,"subject",140,False)
        topic=_text(topic,"topic",180,False)
        limit=max(1,min(int(limit),200))
        with self.db() as conn:
            concepts=self._rows(conn,subject)
            if topic:concepts=[c for c in concepts if c["topic"].casefold()==topic.casefold()]
            states=[self._knowledge_in_conn(conn,c["id"]) for c in concepts[:limit]]
        return {"count":len(states),"topics":states,"explanation":
                "Only observed attempts. Never interpret a single correct exercise as long-term mastery."}

    def history(self,subject:str="",topic:str="",limit:int=50)->dict:
        subject=_text(subject,"subject",140,False)
        topic=_text(topic,"topic",180,False)
        limit=max(1,min(int(limit),200))
        query="""SELECT a.id,a.observed_at,a.outcome,a.assessment,a.exercise_ref,a.evidence,
                        a.difficulties,a.source_path,c.subject,c.topic
                 FROM attempts a JOIN concepts c ON c.id=a.concept_id WHERE 1=1"""
        vals=[]
        if subject:query+=" AND c.subject_key=?";vals.append(_key(subject))
        if topic:query+=" AND c.topic_key=?";vals.append(_key(topic))
        query+=" ORDER BY a.observed_at DESC,a.rowid DESC LIMIT ?";vals.append(limit)
        with self.db() as conn:
            rows=[dict(x) for x in conn.execute(query,vals)]
        for row in rows:
            row["difficulties"]=json.loads(row["difficulties"])
            row["outcome_label"]=OUTCOME_LABELS[row["outcome"]]
        return {"attempts":rows,"count":len(rows)}

    def reviews(self,subject:str="",limit:int=30)->dict:
        states=self.knowledge(subject,limit=200)["topics"]
        due=[s for s in states if s["review_due"]]
        due.sort(key=lambda s:(0 if s["status"]=="needs_practice" else
                               1 if s["status"]=="needs_independent_test" else 2,
                               s["recommended_review_at"] or ""))
        return {"due":due[:max(1,min(int(limit),100))],"count":len(due),
                "method":"Spaced review is a heuristic; test without hints and with a different problem."}

    def record_lesson(self,subject:str,lesson:str,study_stage:str,
                      scheduled_for:str="",taught_on:str="",evidence:str="",
                      source_kind:str="") -> dict:
        """Only confirmed teaching can count as behind; a new PDF never proves a lecture happened."""
        subject=_text(subject,"subject",140)
        lesson=_text(lesson,"lesson",180)
        study_stage=_text(study_stage,"study_stage",30)
        if study_stage not in STUDY_STAGES:raise ValueError("invalid study_stage")
        scheduled_for=_date(scheduled_for,"scheduled_for")
        taught_on=_date(taught_on,"taught_on")
        source_kind=_text(source_kind,"source_kind",50,False)
        evidence=_text(evidence,"evidence",500,False)
        if (scheduled_for or taught_on) and (not source_kind or source_kind not in LESSON_SOURCES or not evidence):
            raise ValueError("lecture dates require verified evidence and source_kind")
        if taught_on and source_kind=="official_timetable":
            raise ValueError("a planned timetable alone cannot confirm lecture actually happened")
        now=self._now().isoformat()
        with self.db() as conn:
            old=conn.execute("SELECT * FROM lessons WHERE subject_key=? AND lesson_key=?",
                             (_key(subject),_key(lesson))).fetchone()
            if old:
                # A blank parameter does not erase known confirmed dates.
                scheduled_for=scheduled_for or old["scheduled_for"] or ""
                taught_on=taught_on or old["taught_on"] or ""
                if study_stage=="not_started" and old["study_stage"]!="not_started":
                    study_stage=old["study_stage"]
                source_kind=source_kind or old["source_kind"]
                evidence=evidence or old["evidence"]
            studied_on = (now[:10] if study_stage=="completed" else
                          old["studied_on"] if old else None)
            conn.execute("""INSERT INTO lessons(subject,subject_key,lesson,lesson_key,
                scheduled_for,taught_on,studied_on,study_stage,source_kind,evidence,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(subject_key,lesson_key) DO UPDATE SET
                scheduled_for=excluded.scheduled_for,taught_on=excluded.taught_on,
                studied_on=excluded.studied_on,study_stage=excluded.study_stage,
                source_kind=excluded.source_kind,evidence=excluded.evidence,updated_at=excluded.updated_at""",
                (subject,_key(subject),lesson,_key(lesson),scheduled_for or None,
                 taught_on or None,studied_on,study_stage,source_kind,evidence,now))
        return {"recorded":True,"subject":subject,"lesson":lesson,
                "study_stage":study_stage,"scheduled_for":scheduled_for or None,
                "taught_on":taught_on or None,"note":"No mastery inferred from lesson completion."}

    def course_progress(self,subject:str="")->dict:
        subject=_text(subject,"subject",140,False)
        query="SELECT * FROM lessons"
        vals=[]
        if subject:query+=" WHERE subject_key=?";vals.append(_key(subject))
        query+=" ORDER BY subject_key,coalesce(taught_on,scheduled_for,'9999'),lesson_key"
        with self.db() as conn:
            rows=[dict(x) for x in conn.execute(query,vals)]
        today=self._now().date()
        behind=[]
        upcoming=[]
        for row in rows:
            if row["taught_on"] and row["taught_on"]<=today.isoformat() and row["study_stage"]!="completed":
                behind.append({"subject":row["subject"],"lesson":row["lesson"],
                               "taught_on":row["taught_on"],"days_behind":
                               (today-date.fromisoformat(row["taught_on"])).days,
                               "study_stage":row["study_stage"],"evidence":row["evidence"]})
            elif row["scheduled_for"] and not row["taught_on"]:
                upcoming.append({"subject":row["subject"],"lesson":row["lesson"],
                                 "scheduled_for":row["scheduled_for"],
                                 "status":"scheduled_not_confirmed_taught"})
        return {"lessons":len(rows),"studied":sum(x["study_stage"]=="completed" for x in rows),
                "behind_count":len(behind),"behind":behind,"scheduled_unconfirmed":upcoming,
                "warning":"Backlog counts only confirmed past lessons, not Moodle upload dates."}

    def study_plan(self,subject:str="",minutes:int=60)->dict:
        minutes=max(10,min(int(minutes),240))
        due=self.reviews(subject,limit=100)["due"]
        behind=self.course_progress(subject)["behind"]
        tasks=[]
        remaining=minutes
        for state in due:
            mins=10 if state["status"] in {"needs_practice","needs_independent_test"} else 8
            if remaining<mins:break
            tasks.append({"kind":"review","subject":state["subject"],"topic":state["topic"],
                          "minutes":mins,"reason":state["interpretation"],
                          "instruction":"Test independently on a different task, without seeing the solution."})
            remaining-=mins
        for lesson in sorted(behind,key=lambda x:x["days_behind"],reverse=True):
            if remaining<15:break
            mins=min(25,remaining)
            tasks.append({"kind":"catch_up","subject":lesson["subject"],"lesson":lesson["lesson"],
                          "minutes":mins,"reason":f"Verified lesson, {lesson['days_behind']} days ago."})
            remaining-=mins
        return {"budget_minutes":minutes,"allocated_minutes":minutes-remaining,
                "tasks":tasks,"unallocated_minutes":remaining,
                "warning":"This plan covers only recorded learning evidence and verified lecture progress; it cannot know untracked lessons."}
