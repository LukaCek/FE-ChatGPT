"""Filesystem boundary: original FE material is always read-only."""
from __future__ import annotations
import os
from pathlib import Path
from datetime import datetime, timezone

ALLOWED = frozenset({'.pdf', '.txt', '.md', '.csv', '.json', '.tex', '.py', '.log'})
SKIP_DIRS = frozenset({'.stversions', '.git', '__pycache__', '.trash', '.Trash-1000', '@eaDir', '.syncthing'} )
MAX_FILE_BYTES = 100 * 1024 * 1024

class StudyFiles:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise ValueError(f'FE mapica ne obstaja: {self.root}')

    def resolve(self, path: str, is_dir: bool = False) -> Path:
        if not isinstance(path, str) or '\x00' in path:
            raise ValueError('Neveljavna pot')
        raw = Path(path)
        parts = raw.parts
        if raw.is_absolute() or '..' in parts:
            raise ValueError('Absolutne poti in dostop izven FE niso dovoljeni')
        # Inspect path components BEFORE resolving; otherwise internal symlinks disappear.
        curr = self.root
        for part in parts:
            if part != '.':
                curr = curr / part
                if curr.is_symlink():
                    raise ValueError('Simbolne povezave niso dovoljene')
        if any(x.startswith('.') or x in SKIP_DIRS for x in parts if x not in ('.', '..')):
            raise ValueError('Skrite/sistemske poti niso dovoljene')
        try:
            p = (self.root / path).resolve(strict=True)
        except (FileNotFoundError, OSError):
            raise ValueError('Datoteka ali mapa ne obstaja') from None
        if not p.is_relative_to(self.root):
            raise ValueError('Dostop izven FE mape ni dovoljen')
        if is_dir:
            if not p.is_dir():
                raise ValueError('Pot ni mapa')
        else:
            if not p.is_file() or p.suffix.lower() not in ALLOWED:
                raise ValueError('Nepodprta datoteka')
            if p.stat().st_size > MAX_FILE_BYTES:
                raise ValueError('Datoteka presega 100 MiB')
        return p

    def iter_files(self, limit: int = 5000):
        n=0
        for base, dirs, names in os.walk(self.root, followlinks=False):
            dirs[:] = sorted(x for x in dirs if not x.startswith('.') and x not in SKIP_DIRS and not (Path(base)/x).is_symlink())
            for name in sorted(names):
                if name.startswith('.') or Path(name).suffix.lower() not in ALLOWED:
                    continue
                p = Path(base) / name
                if p.is_symlink():
                    continue
                try:
                    p = self.resolve(p.relative_to(self.root).as_posix())
                except (ValueError, PermissionError, OSError):
                    continue
                yield p
                n += 1
                if n >= limit:
                    return

    def info(self, p: Path) -> dict:
        s = p.stat()
        return {'path': p.relative_to(self.root).as_posix(), 'size_bytes': s.st_size,
                'modified_utc': datetime.fromtimestamp(s.st_mtime, tz=timezone.utc).isoformat()}


def is_notebook(path: str, pages: int) -> bool:
    s = path.casefold()
    return pages >= 200 and (('zvezek' in s) or ('zapiski' in s) or ('beležk' in s) or ('notes' in s))
