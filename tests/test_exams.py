"""Per-exam readiness reflects recorded evidence, not invented grades or scope."""
from datetime import datetime, timedelta, timezone

import pytest

from fe_gradiva.study import StudyTracker


class Clock:
    def __init__(self):
        self.time=datetime(2026,10,21,9,0,tzinfo=timezone.utc)
    def __call__(self):
        return self.time
    def move(self,days):
        self.time+=timedelta(days=days)


@pytest.fixture
def fresh(tmp_path):
    clock=Clock()
    t=StudyTracker(tmp_path/'study.sqlite3',clock=clock)
    return t,clock


def assessment(t,topics=None,name='1. kolokvij',kind='kolokvij',date=''):
    return t.set_exam_assessment(
        '01_MAT',name,kind,scheduled_for=date,topics=topics,
        source_kind='user_confirmed' if topics or date else '',
        evidence='Izrecno potrjeno gradivo za preverjanje.' if topics or date else '')


def attempt(t,topic,outcome='independent',assessment='practice',ref='naloga A'):
    return t.record_attempt('01_MAT',topic,outcome,
        'Opazovan samostojen poskus.' if outcome=='independent' else
        'Reševanje s pomočjo ali napačen rezultat.',
        assessment=assessment,exercise_ref=ref)


def by_name(t,name='1. kolokvij'):
    return next(a for a in t.exam_readiness('01_MAT')['assessments'] if a['name']==name)


def test_all_subjects_including_unconfigured_have_overview(fresh):
    t,_=fresh
    report=t.exam_readiness(known_subjects=['01_MAT','02_FIZ_MiT','03_PROG','04_ELE'])
    assert report['count']==0
    assert report['unconfigured_subjects']==['01_MAT','02_FIZ_MiT','03_PROG','04_ELE']


def test_unknown_exam_scope_does_not_fabricate_readiness_pct(fresh):
    t,_=fresh
    assessment(t)
    row=by_name(t)
    assert row['scope_known'] is False
    assert row['independent_coverage_pct'] is None
    assert row['delayed_recall_coverage_pct'] is None
    assert row['week_later_coverage_pct'] is None
    assert row['readiness']=='scope_unknown'


def test_scope_requires_evidence_and_unobserved_topics_are_not_passes(fresh):
    t,_=fresh
    with pytest.raises(ValueError,match='source_kind and evidence'):
        t.set_exam_assessment('01_MAT','Kolokvij','kolokvij',topics=['Limite'])
    assert t.exam_readiness()['count']==0
    assessment(t,['Limite','Odvodi'],date='2026-10-30')
    row=by_name(t)
    assert row['days_until']==9
    assert row['counts']['untested']==2
    assert row['independent_coverage_pct']==0
    assert row['readiness']=='requires_practice'
    assert 'likelihood of passing' in t.exam_readiness()['warning']


def test_one_independent_is_coverage_not_confirmed_retention(fresh):
    t,_=fresh
    assessment(t,['Limite','Odvodi'])
    attempt(t,'Limite')
    row=by_name(t)
    assert row['counts']['independent']==1
    assert row['independent_coverage_pct']==50
    assert row['delayed_recall_coverage_pct']==0
    assert row['week_later_coverage_pct']==0
    assert row['topics'][0]['status']=='first_independent'
    assert row['topics'][1]['status']=='unassessed'


def test_delayed_recall_progression_and_failure_regression(fresh):
    t,clock=fresh
    assessment(t,['Limite'])
    attempt(t,'Limite',ref='naloga 1')
    clock.move(2)
    attempt(t,'Limite',assessment='delayed_recall',ref='naloga 2')
    row=by_name(t)
    assert row['delayed_recall_coverage_pct']==100
    assert row['week_later_coverage_pct']==0
    assert row['readiness']=='needs_week_later_recall'
    clock.move(8)
    attempt(t,'Limite',assessment='delayed_recall',ref='naloga 3')
    row=by_name(t)
    assert row['week_later_coverage_pct']==100
    assert row['readiness']=='good_observed_evidence'
    attempt(t,'Limite',outcome='incorrect',ref='naloga 4')
    row=by_name(t)
    assert row['readiness']=='requires_practice'
    assert row['counts']['needs_practice']==1
    assert row['week_later_coverage_pct']==0


def test_self_report_and_guidance_do_not_count(fresh):
    t,_=fresh
    assessment(t,['Limite','Odvodi'])
    attempt(t,'Limite',outcome='guided')
    attempt(t,'Odvodi',outcome='guided',assessment='self_report')
    row=by_name(t)
    assert row['independent_coverage_pct']==0
    assert row['counts']['needs_practice']==1
    assert row['counts']['untested']==1


def test_no_cross_subject_or_wrong_topic_matching(fresh):
    t,_=fresh
    assessment(t,['Limite','Odvodi'])
    t.record_attempt('04_ELE','Limite','independent','Samostojen poskus.',exercise_ref='E1')
    attempt(t,'Limite in odvodi')
    assert by_name(t)['independent_coverage_pct']==0


def test_idempotent_updates_preserve_scope_date_and_study_history(fresh):
    t,clock=fresh
    first=assessment(t,['Limite'],date='2026-11-01')
    attempt(t,'Limite')
    second=t.set_exam_assessment('01_MAT','1. KOLOKVIJ','kolokvij')
    assert first['id']==second['id']
    row=by_name(t)
    assert row['scheduled_for']=='2026-11-01'
    assert row['scope_topics']==1
    assert row['independent_coverage_pct']==100
    assert t.history('01_MAT')['count']==1
    again=StudyTracker(t.path,clock=clock)
    assert again.exam_readiness('01_MAT')['count']==1
    assert again.knowledge('01_MAT')['count']==1


def test_date_can_be_cleared_and_scope_reset_explicitly(fresh):
    t,_=fresh
    assessment(t,['Limite'],date='2026-11-01')
    t.set_exam_assessment('01_MAT','1. kolokvij','kolokvij',clear_date=True,topics=[])
    row=by_name(t)
    assert row['scheduled_for'] is None
    assert row['scope_known'] is False
    assert row['independent_coverage_pct'] is None


def test_invalid_scope_and_date_are_rejected_before_mutation(fresh):
    t,_=fresh
    for items in [['Limite',' limite '],[''],['A']*121,'Limite']:
        with pytest.raises(ValueError):
            t.set_exam_assessment('01_MAT','Kolokvij','kolokvij',topics=items,
                                  source_kind='user_confirmed',evidence='Potrjeno')
    for kind in ['vaja','izpitni_rok','']:
        with pytest.raises(ValueError):
            t.set_exam_assessment('01_MAT','Kolokvij',kind)
    with pytest.raises(ValueError):
        assessment(t,['Limite'],date='2026-02-30')
    with pytest.raises(ValueError):
        t.set_exam_assessment('01_MAT','Kolokvij','kolokvij',
                              scheduled_for='2026-11-01',clear_date=True)
    assert t.exam_readiness()['count']==0


def test_different_exams_have_separate_scopes(fresh):
    t,_=fresh
    assessment(t,['Limite'],name='1. kolokvij')
    assessment(t,['Odvodi'],name='Izpit',kind='izpit')
    attempt(t,'Limite')
    rows=t.exam_readiness('01_MAT')
    assert rows['count']==2
    exam=t.exam_readiness('01_MAT',kind='izpit')
    assert exam['count']==1
    assert exam['assessments'][0]['independent_coverage_pct']==0
    assert t.exam_readiness('01_MAT',kind='kolokvij')['assessments'][0]['independent_coverage_pct']==100


def test_void_attempt_resets_exam_evidence_without_erasing_exam(fresh):
    t,_=fresh
    assessment(t,['Limite'])
    att=attempt(t,'Limite')
    assert by_name(t)['independent_coverage_pct']==100
    t.void_attempt(att['attempt_id'],'Pozneje ugotovljeno, da je bila pomoč.')
    row=by_name(t)
    assert row['independent_coverage_pct']==0
    assert row['counts']['untested']==1
