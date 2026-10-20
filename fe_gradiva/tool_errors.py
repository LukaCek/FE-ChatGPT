"""Return intentional, privacy-safe tool errors as data, not opaque MCP failures.

Common wrong/missing FE paths should be visible to ChatGPT as an actionable
result. Unexpected programming/SQLite failures are deliberately NOT swallowed.
"""
from __future__ import annotations

from functools import wraps


_MESSAGES = {
    'Datoteka ali mapa ne obstaja': ('not_found','Datoteka ali mapa ne obstaja. Najprej preveri pot z list_files.'),
    'Pot ni mapa': ('not_directory','Zahtevana pot ni mapa.'),
    'Nepodprta datoteka': ('unsupported_file','Pot ne kaže na podprto datoteko.'),
    'Pot mora biti PDF': ('unsupported_file','Za ta ukaz mora biti datoteka PDF.'),
    'PDF required': ('unsupported_file','Za ta ukaz mora biti datoteka PDF.'),
    'Neveljavna številka strani': ('invalid_page','Številka strani je neveljavna.'),
    'PDF je zaščiten z geslom': ('encrypted_pdf','PDF je zaščiten z geslom.'),
    'PDF zahteva geslo': ('encrypted_pdf','PDF je zaščiten z geslom.'),
    'Neveljavna pot': ('invalid_path','Pot ni veljavna.'),
    'Simbolne povezave niso dovoljene': ('forbidden_path','Simbolne povezave niso dovoljene.'),
    'Skrite/sistemske poti niso dovoljene': ('forbidden_path','Skrite in sistemske poti niso dovoljene.'),
    'Absolutne poti in dostop izven FE niso dovoljeni': ('forbidden_path','Dostop izven FE ni dovoljen.'),
    'Dostop izven FE mape ni dovoljen': ('forbidden_path','Dostop izven FE ni dovoljen.'),
    'Datoteka presega 100 MiB': ('too_large','Datoteka presega dovoljeno velikost.'),
}


def error_result(exc: Exception, path: object = '') -> dict:
    """A consistent result without exception internals, host paths or credentials."""
    if isinstance(exc, PermissionError):
        code, message = 'permission_denied', 'Ni dovoljenja za dostop do datoteke ali mape.'
    elif isinstance(exc, FileNotFoundError):
        code, message = _MESSAGES['Datoteka ali mapa ne obstaja']
    elif isinstance(exc, OSError):
        code, message = 'io_error', 'Datoteke trenutno ni mogoče prebrati.'
    else:
        code, message = _MESSAGES.get(str(exc), ('invalid_request','Neveljavna zahteva za datoteko ali stran.'))
    return {
        'ok': False,
        'error': code,
        'message': message,
        'path': path if isinstance(path,str) and len(path)<=500 else '',
    }


def file_tool_errors(*, path_arg: str = 'path', list_output: bool = False):
    """Catch known user/path errors; keep normal return types and valid data."""
    def decorate(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except (ValueError, OSError) as exc:
                # Missing paths and invalid page indexes are not service outages.
                # Do NOT include exception text: it may contain server-local paths.
                requested = kwargs.get(path_arg, args[0] if args else '')
                payload = error_result(exc, requested)
                return [payload] if list_output else payload
        return wrapper
    return decorate
