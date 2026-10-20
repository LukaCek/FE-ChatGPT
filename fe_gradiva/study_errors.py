"""Convert expected study-tracker validation failures into actionable MCP data.

Invalid learner observations never mutate the tracker. Unexpected SQLite or
programming errors remain exceptions so infrastructure failures are not hidden.
"""
from __future__ import annotations

from functools import wraps


def study_error_result(exc: ValueError) -> dict:
    reason = str(exc)
    if reason.startswith('attempt_id already used'):
        code = 'attempt_id_conflict'
        message = 'Ta ID poskusa že pripada drugi učni temi. Uporabi nov ID.'
    elif reason.startswith('attempt_id content mismatch'):
        code = 'attempt_id_conflict'
        message = ('ID poskusa že obstaja z drugačnimi podatki. '
                   'Za popravek uporabi void_learning_attempt in nov ID.')
    elif ('Datoteka ali mapa ne obstaja' in reason or
          'Nepodprta datoteka' in reason or
          'Neveljavna pot' in reason or
          'Simbolne povezave niso dovoljene' in reason):
        code = 'invalid_source_path'
        message = ('source_path mora biti obstoječa dovoljena pot znotraj FE. '
                   'Za nalogo samo iz klepeta ga pusti praznega.')
    elif 'invalid outcome or assessment' in reason:
        code = 'invalid_outcome'
        message = ('outcome mora biti incorrect, guided, independent_with_difficulty '
                   'ali independent; assessment mora biti practice, delayed_recall '
                   'ali self_report.')
    elif 'self_report cannot claim observed independence' in reason:
        code = 'invalid_assessment'
        message = 'Samoocena ne dokazuje samostojno rešene naloge.'
    elif ('attempt_id' in reason and 'too long' in reason):
        code = 'invalid_attempt_id'
        message = 'attempt_id mora biti kratek stabilen identifikator.'
    else:
        code = 'invalid_study_input'
        message = ('Neveljaven podatek za evidenco učenja. Preveri obvezna polja, '
                   'omejitve dolžine in dovoljene vrednosti.')
    return {'ok': False, 'recorded': False, 'error': code, 'message': message}


def study_tool_errors(fn):
    """Catch user-input mistakes, not disk, DB, network or programmer failures."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ValueError as exc:
            return study_error_result(exc)
    return wrapper
