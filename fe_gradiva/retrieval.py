"""Source-grounded document discovery; never substitutes ambiguous files silently."""
from __future__ import annotations
import re
from pathlib import PurePosixPath

SUBJECTS = {'mat': '01_MAT', 'matematika':'01_MAT', 'ele': '04_ELE',
            'elektrotehnika': '04_ELE', 'fizika':'02_FIZ_MiT', 'fiz':'02_FIZ_MiT',
            'programiranje':'03_PROG', 'prog':'03_PROG'}


def kind(path: str) -> str:
    low='/' + path.casefold() + '/'
    if '/gradivo/' in low: return 'professor_material'
    if '/vaje/' in low: return 'exercises'
    if '/predavanja/' in low: return 'lecture_notes'
    if '/zapiski/' in low: return 'student_notes'
    return 'other'


def subject_path(query: str) -> str:
    q=query.casefold().strip()
    return SUBJECTS.get(q, query)


def document_score(path: str, query: str, populated: int, pages: int, kind_hint: str='') -> tuple[float, list[str]]:
    name=PurePosixPath(path).name.casefold()
    q=query.strip().casefold()
    tokens=re.findall(r'\w+',q)
    reasons=[]
    score=0.0
    if q == name or q == PurePosixPath(name).stem: score+=90;reasons.append('exact_name')
    elif q and q in name: score+=42;reasons.append('filename')
    elif tokens and all(t in path.casefold() for t in tokens): score+=24;reasons.append('name_tokens')
    elif tokens and any(t in path.casefold() for t in tokens): score+=8;reasons.append('partial_name')
    if not reasons: return 0.0, []
    if kind_hint and kind_hint==kind(path): score+=25;reasons.append('matching_kind')
    if pages>=200 and populated==0: score-=55;reasons.append('empty_template')
    elif populated: score+=8;reasons.append('populated_pages')
    return score, reasons


EXERCISE_HEADING = re.compile(
    r'(?im)^\s*(?:POGLAVJE\s*:\s*\d+\s*)?'
    r'(?:VAJA|NALOGA|EXERCISE)\s*[:#]?\s*'
    r'(?P<number>\d{1,3}(?:\.\d{1,3}){1,2})'
    r'(?![\d.])(?:\s*\([a-zA-Z]\))?'
)


def exercise_parts(ref: str) -> tuple[str, str | None]:
    match=re.fullmatch(r'(\d{1,3}(?:\.\d{1,3}){1,2})(?:\(([a-zA-Z])\))?',ref.strip())
    if not match:raise ValueError('Exercise reference must look like 1.1 or 1.1(b)')
    return match.group(1), (match.group(2).lower() if match.group(2) else None)


def SUBPART_MARKER(letter: str) -> re.Pattern:
    return re.compile(r'(?im)(?<![\w])(?:\(\s*'+re.escape(letter)+r'\s*\)|'+re.escape(letter)+r'\s*\))')


def exercise_pattern(ref: str):
    """Backward-compatible exact reference helper, not used for verification."""
    number, subpart=exercise_parts(ref)
    suffix=r'\s*\(\s*'+subpart+r'\s*\)' if subpart else ''
    return re.compile(r'(?<![\d.])'+re.escape(number)+suffix+r'(?![\d.])',flags=re.IGNORECASE)
