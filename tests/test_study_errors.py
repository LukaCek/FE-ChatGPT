"""Structured study errors should be actionable without polluting the DB."""
import inspect
from pathlib import Path

import pytest

from fe_gradiva.study import StudyTracker
from fe_gradiva.study_errors import study_error_result,study_tool_errors


def test_invalid_subject_never_writes(tmp_path):
    tracker=StudyTracker(tmp_path/'study.db')

    @study_tool_errors
    def record(subject,topic,outcome,evidence,attempt_id):
        return tracker.record_attempt(subject,topic,outcome,evidence,attempt_id=attempt_id)

    bad=record('', 'Limits', 'independent', 'Example', 'probe-test-1')
    assert bad['ok'] is False
    assert bad['recorded'] is False
    assert bad['error']=='invalid_study_input'
    assert tracker.history()['count']==0
    assert list(inspect.signature(record).parameters)==[
        'subject','topic','outcome','evidence','attempt_id']


def test_valid_attempt_is_recorded_and_duplicate_id_remains_safe(tmp_path):
    tracker=StudyTracker(tmp_path/'study.db')

    @study_tool_errors
    def record(subject,topic,outcome,evidence,attempt_id):
        return tracker.record_attempt(subject,topic,outcome,evidence,attempt_id=attempt_id)

    initial=record('01_MAT','Limite','independent','Odgovor 11','lim-123')
    assert initial['recorded'] is True
    changed=record('01_MAT','Limite','incorrect','Odgovor 12','lim-123')
    assert changed['ok'] is False
    assert changed['error']=='attempt_id_conflict'
    assert tracker.history()['count']==1
    assert tracker.history()['attempts'][0]['outcome']=='independent'


def test_invalid_source_path_does_not_leak_absolute_host_path():
    e=study_error_result(ValueError('Datoteka ali mapa ne obstaja: /private/secret'))
    assert e['error']=='invalid_source_path'
    assert '/private' not in str(e)
    assert e['recorded'] is False


def test_unexpected_sqlite_and_runtime_errors_not_silenced():
    @study_tool_errors
    def bug():
        raise RuntimeError('unexpected problem')
    with pytest.raises(RuntimeError):
        bug()


def test_clear_messages_for_attempt_conflict_and_outcome():
    a=study_error_result(ValueError('invalid outcome or assessment'))
    assert a['error']=='invalid_outcome'
    assert 'guided' in a['message']
    b=study_error_result(ValueError('attempt_id already used for another concept'))
    assert b['error']=='attempt_id_conflict'


def test_invalid_lesson_progress_returns_error(tmp_path):
    tracker=StudyTracker(tmp_path/'study.db')

    @study_tool_errors
    def lesson(stage):
        return tracker.record_lesson('01_MAT','Limite',stage)

    bad=lesson('passed')
    assert bad['ok'] is False
    assert tracker.course_progress()['lessons']==0
