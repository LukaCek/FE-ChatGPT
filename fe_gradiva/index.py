"""Incremental, local-only index. Never modifies files under FE_ROOT."""
from __future__ import annotations
import hashlib
from contextlib import contextmanager
import json
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any
import pymupdf
from .core import StudyFiles, is_notebook
from .retrieval import kind, document_score, subject_path, exercise_pattern

SCHEMA = '''
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS files (
 path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
 pages INTEGER NOT NULL DEFAULT 0, notebook INTEGER NOT NULL DEFAULT 0,
 indexed_utc TEXT NOT NULL, error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS pages (
 path TEXT NOT NULL, page INTEGER NOT NULL, fingerprint TEXT NOT NULL,
 populated INTEGER NOT NULL DEFAULT 1, text TEXT NOT NULL DEFAULT '',
 changed_utc TEXT, label TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(path,page), FOREIGN KEY(path) REFERENCES files(path) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS page_fts USING fts5(path UNINDEXED, page UNINDEXED, text, label, tokenize='unicode61 remove_diacritics 2');
CREATE INDEX IF NOT EXISTS pages_path_pop ON pages(path,populated,page);
'''

def utcnow():
    return datetime.now(timezone.utc).isoformat()


def text_terms(s: str) -> list[str]:
    # Bound the FTS query; quote each token rather than interpolating FTS syntax.
    return re.findall(r'[^\W_]+', s.casefold(), re.UNICODE)[:12]


def populated_by_ink(page: pymupdf.Page) -> bool:
    """Heuristic for graph-paper notebooks: ignore faint grid/footer page number.

    Renders a small grayscale interior, counts dark ink. Blank numbered A4 graph pages
    usually have no pixels below 150 (their grid is light gray). Conservative
    classification: when ambiguous, keep the page instead of discarding it.
    """
    r=page.rect
    clip=pymupdf.Rect(r.x0+r.width*.045, r.y0+r.height*.015,
                      r.x1-r.width*.04, r.y1-r.height*.075)
    if clip.is_empty:
        return True
    pix=page.get_pixmap(matrix=pymupdf.Matrix(.48,.48),clip=clip,colorspace=pymupdf.csGRAY,alpha=False)
    data=pix.samples
    # Two cutoffs help retain lighter pencil notes without mistaking the faint grid.
    dark=sum(1 for a in data[::2] if a < 153)
    if dark >= 30:
        return True
    soft=sum(1 for a in data[::2] if a < 181)
    if soft >= 110:
        return True
    # Some students write only in light coloured pen: detect chromatic strokes.
    rgb=page.get_pixmap(matrix=pymupdf.Matrix(.35,.35),clip=clip,colorspace=pymupdf.csRGB,alpha=False)
    pixels=rgb.samples
    colored=0
    for i in range(0,len(pixels)-2,12):
        r,g,b=pixels[i],pixels[i+1],pixels[i+2]
        if max(r,g,b)-min(r,g,b)>50 and min(r,g,b)<225:
            colored+=1
            if colored>=15:
                return True
    return False


def fingerprint(page: pymupdf.Page) -> str:
    # Fingerprint visually rather than PDF bytes to survive metadata-only resaves.
    pix=page.get_pixmap(matrix=pymupdf.Matrix(.42,.42),colorspace=pymupdf.csGRAY,alpha=False)
    return hashlib.blake2b(pix.samples,digest_size=12).hexdigest()

class Index:
    def __init__(self, files: StudyFiles, db_path: str | Path):
        self.files=files
        self.db_path=Path(db_path)
        self.db_path.parent.mkdir(parents=True,exist_ok=True)
        self._gate=threading.Lock()
        self._status_lock=threading.Lock()
        self._status={'running':False,'last_error':'','last_scan_utc':None,'last_duration_sec':None,'files_updated':0}
        with self.db() as c:
            c.executescript(SCHEMA)
            c.execute('PRAGMA journal_mode=WAL')

    @contextmanager
    def db(self):
        conn=sqlite3.connect(self.db_path,timeout=15)
        try:
            conn.row_factory=sqlite3.Row
            conn.execute('PRAGMA foreign_keys=ON')
            conn.execute('PRAGMA busy_timeout=15000')
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def stats(self):
        with self.db() as c:
            counts=c.execute('SELECT (SELECT count(*) FROM files) files,(SELECT count(*) FROM pages WHERE populated=1) populated_pages,(SELECT count(*) FROM pages WHERE populated=0) blank_pages,(SELECT count(*) FROM pages WHERE label != \'\') labeled_pages').fetchone()
            errors=[dict(x) for x in c.execute('SELECT path,error FROM files WHERE error!=\'\' LIMIT 10')]
        with self._status_lock:
            s=dict(self._status)
        return {**dict(counts),**s,'errors':errors,'read_only_fe':True}

    def scan(self):
        if not self._gate.acquire(blocking=False):
            return {'running':True}
        began=time.monotonic()
        count=0; errors=[]
        with self._status_lock: self._status['running']=True
        try:
            seen=set()
            for p in self.files.iter_files():
                rel=p.relative_to(self.files.root).as_posix()
                seen.add(rel)
                try:
                    st=p.stat()
                    with self.db() as c:
                        existing=c.execute('SELECT mtime_ns,size,error FROM files WHERE path=?',(rel,)).fetchone()
                    if existing and existing['mtime_ns']==st.st_mtime_ns and existing['size']==st.st_size and not existing['error']:
                        continue
                    self._index_file(p,rel,st)
                    count+=1
                except Exception as e:
                    msg=f'{type(e).__name__}: {str(e)[:160]}'
                    errors.append({'path':rel,'error':msg})
                    with self.db() as c:
                        c.execute('DELETE FROM page_fts WHERE path=?',(rel,))
                        c.execute('INSERT OR REPLACE INTO files(path,size,mtime_ns,pages,notebook,indexed_utc,error) VALUES (?,?,?,?,?,?,?)',
                                  (rel,0,0,0,0,utcnow(),msg))
            with self.db() as c:
                old=[r[0] for r in c.execute('SELECT path FROM files') if r[0] not in seen]
                for path in old:
                    c.execute('DELETE FROM page_fts WHERE path=?',(path,))
                    c.execute('DELETE FROM files WHERE path=?',(path,))
            with self._status_lock:
                self._status.update(last_scan_utc=utcnow(),last_duration_sec=round(time.monotonic()-began,2),files_updated=count,last_error='; '.join(f'{x["path"]}: {x["error"]}' for x in errors[:3]))
            return {'updated_files':count,'errors':errors}
        except Exception as e:
            with self._status_lock: self._status['last_error']=f'{type(e).__name__}: {e}'
            raise
        finally:
            with self._status_lock: self._status['running']=False
            self._gate.release()

    def _index_file(self,p:Path,rel:str,stat):
        pages=[]; n=0; notebook=False
        with self.db() as c:
            old_page_cache={r['page']:r for r in c.execute(
                'SELECT page,fingerprint,populated,text FROM pages WHERE path=?',(rel,))}
        if p.suffix.lower()=='.pdf':
            with pymupdf.open(p) as pdf:
                if pdf.needs_pass: raise ValueError('PDF zahteva geslo')
                n=len(pdf)
                if n>3000: raise ValueError('PDF ima več kot 3000 strani')
                notebook=is_notebook(rel,n)
                for i,page in enumerate(pdf):
                    fp=fingerprint(page)
                    previous=old_page_cache.get(i+1)
                    if previous and previous['fingerprint']==fp:
                        # A full PDF rewrite often changes file mtime but not every page.
                        # Reuse page content and blank detection after visual fingerprint matches.
                        pages.append((i+1,fp,previous['populated'],previous['text']))
                        continue
                    ink=populated_by_ink(page) if notebook else True
                    txt=page.get_text(sort=True).strip() if ink else ''
                    # A page number by itself is not searchable lecture content.
                    if notebook and txt.isdigit() and len(txt)<5: txt=''
                    pages.append((i+1,fp,int(ink),txt[:20000]))
        else:
            text=p.read_text(encoding='utf-8',errors='replace')[:2000000]
            n=1; pages=[(1,hashlib.blake2b(text.encode(),digest_size=12).hexdigest(),1,text)]
        now=utcnow()
        with self.db() as c:
            # Retain handwritten page labels if a PDF is saved again.
            old={r['page']:(r['fingerprint'],r['changed_utc'],r['label']) for r in
                 c.execute('SELECT page,fingerprint,changed_utc,label FROM pages WHERE path=?',(rel,))}
            c.execute('INSERT INTO files (path,size,mtime_ns,pages,notebook,indexed_utc,error) VALUES (?,?,?,?,?,?,\'\') ON CONFLICT(path) DO UPDATE SET size=excluded.size,mtime_ns=excluded.mtime_ns,pages=excluded.pages,notebook=excluded.notebook,indexed_utc=excluded.indexed_utc,error=\'\'',
                      (rel,stat.st_size,stat.st_mtime_ns,n,int(notebook),now))
            c.execute('DELETE FROM page_fts WHERE path=?',(rel,))
            c.execute('DELETE FROM pages WHERE path=?',(rel,))
            for page,fp,ink,txt in pages:
                old_data=old.get(page)
                changed=(now if old_data and old_data[0]!=fp else (old_data[1] if old_data else None))
                label=old_data[2] if old_data and old_data[0]==fp else ''
                c.execute('INSERT INTO pages(path,page,fingerprint,populated,text,changed_utc,label) VALUES (?,?,?,?,?,?,?)',(rel,page,fp,ink,txt,changed,label))
                if ink and (txt or label):
                    c.execute('INSERT INTO page_fts(path,page,text,label) VALUES(?,?,?,?)',(rel,page,txt,label))

    def populated_pages(self,path:str,limit:int=100,only_unlabeled:bool=False):
        p=self.files.resolve(path)
        rel=p.relative_to(self.files.root).as_posix()
        if p.suffix.lower()!='.pdf': raise ValueError('Pot mora biti PDF')
        with self.db() as c:
            rows=c.execute('SELECT page,changed_utc,label FROM pages WHERE path=? AND populated=1 '+ ('AND label=\'\' ' if only_unlabeled else '')+'ORDER BY page LIMIT ?', (rel,min(max(limit,1),1000))).fetchall()
            f=c.execute('SELECT pages,notebook FROM files WHERE path=?',(rel,)).fetchone()
        return {'path':rel,'total_pages':f['pages'] if f else None, 'notebook':bool(f['notebook']) if f else None,
                'populated':[dict(r) for r in rows], 'index_pending':f is None}

    def search(self,query:str,subject:str='',limit:int=20):
        subject=subject_path(subject)
        if not query or len(query)>160: raise ValueError('Poizvedba mora imeti 1–160 znakov')
        terms=text_terms(query)
        limit=min(max(limit,1),50)
        if not terms: return []
        match=' OR '.join(f'"{word}"*' for word in terms)
        with self.db() as c:
            rows=c.execute('''SELECT f.path, f.page, snippet(page_fts,2,'[',']',' … ',22) snippet,
                                bm25(page_fts,0,0,1.0,3.0) rank, p.label
                              FROM page_fts f JOIN pages p ON p.path=f.path AND p.page=CAST(f.page AS INTEGER)
                              WHERE page_fts MATCH ? ORDER BY rank LIMIT ?''',(match,limit*8)).fetchall()
            names=c.execute('SELECT path FROM files WHERE path LIKE ? LIMIT 30',('%'+query+'%',)).fetchall()
        results=[];seen=set()
        for row in rows:
            d=dict(row)
            if subject and subject.casefold() not in d['path'].casefold(): continue
            key=(d['path'],int(d['page']))
            if key in seen:continue
            seen.add(key)
            results.append({'path':d['path'],'page':int(d['page']),'snippet':d['snippet'], 'label':d['label'],'match':'indexed_content'})
            if len(results)>=limit: return results
        for row in names:
            path=row['path']
            if subject and subject.casefold() not in path.casefold():continue
            if (path,0) not in seen:
                results.append({'path':path,'page':None,'match':'filename'})
                seen.add((path,0))
            if len(results)>=limit:break
        return results

    def resolve_document(self, query:str, subject:str='', kind_hint:str='', limit:int=8):
        """Rank candidates; never claim ambiguous Zvezek.pdf is definitively the right file."""
        if not query.strip(): raise ValueError('Provide a document name or query')
        subject=subject_path(subject)
        with self.db() as c:
            rows=c.execute('''SELECT f.path,f.pages,f.notebook,f.mtime_ns,
                count(CASE WHEN p.populated=1 THEN 1 END) populated
                FROM files f LEFT JOIN pages p ON p.path=f.path
                WHERE f.error='' GROUP BY f.path''').fetchall()
        items=[]
        for r in rows:
            if subject and subject.casefold() not in r['path'].casefold():continue
            score,reasons=document_score(r['path'],query,r['populated'],r['pages'],kind_hint)
            if score<=0 and 'empty_template' not in reasons:continue
            items.append({'path':r['path'],'score':score,'match_reasons':reasons,
                'kind':kind(r['path']),'pages':r['pages'],'populated_pages':r['populated'],
                'is_blank_template':r['pages']>=200 and r['populated']==0})
        items.sort(key=lambda x:(-x['score'],x['path']))
        items=items[:max(1,min(int(limit),20))]
        ambiguous=len(items)>1 and items[0]['score']-items[1]['score']<20
        return {'candidates':items,'ambiguous':ambiguous,
            'guidance':'Open the actual file/page. Do not infer contents from name or mtime.'}

    def find_exercise(self, reference:str,subject:str='',limit:int=12):
        """Ground exercise number in indexed original text; flag image-only PDFs as unverified."""
        pattern=exercise_pattern(reference)
        subject=subject_path(subject)
        with self.db() as c:
            rows=c.execute('''SELECT p.path,p.page,p.text,f.notebook FROM pages p JOIN files f ON f.path=p.path
                WHERE p.text!='' AND f.error='' ORDER BY f.mtime_ns DESC''').fetchall()
            image_rows=c.execute('''SELECT f.path,f.pages FROM files f WHERE f.error='' AND f.path LIKE '%.pdf'
                AND NOT EXISTS (SELECT 1 FROM pages p WHERE p.path=f.path AND length(trim(p.text))>6)
                ORDER BY f.mtime_ns DESC LIMIT 30''').fetchall()
        found=[]
        for r in rows:
            if subject and subject.casefold() not in r['path'].casefold():continue
            for m in list(pattern.finditer(r['text']))[:2]:
                span=r['text'][max(0,m.start()-110):min(len(r['text']),m.end()+420)]
                found.append({'path':r['path'],'page':r['page'],'text_excerpt':span,
                    'kind':kind(r['path']),'verified_reference':True})
                if len(found)>=max(1,min(int(limit),30)): break
            if len(found)>=max(1,min(int(limit),30)):break
        found.sort(key=lambda r: (kind(r['path'])!='exercises',r['path'],r['page']))
        candidates=[{'path':r['path'],'pages':r['pages'],'verification_required':'visual'}
            for r in image_rows if not subject or subject.casefold() in r['path'].casefold()]
        return {'reference':reference,'matches':found,
            'image_only_candidates':candidates[:5] if not found else [],
            'warning':'A matching exercise number does not confirm it is the intended course. Verify original page and values; never invent numbers.'}

    def study_context(self,topic:str,subject:str='',limit:int=12):
        items=self.search(topic,subject,limit=limit)
        notes=[];sources=[]
        for item in items:
            path=item['path'].casefold()
            (sources if '/gradivo/' in path else notes).append(item)
        # Do not return random handwriting as if it matches the topic.
        # Use populated_pages to inspect unlabelled handwritten pages explicitly.
        return {'topic':topic,'notes':notes[:limit],'professor_materials':sources[:limit],
                'caution':'Only indexed text or verified page annotations match topics. Unlabelled handwriting cannot be searched by meaning; use populated_pages and inspect visually.'}

    def annotate(self,path:str,page:int,label:str):
        p=self.files.resolve(path)
        if p.suffix.lower()!='.pdf':raise ValueError('Oznake so samo za PDF strani')
        if len(label)>400 or not label.strip():raise ValueError('Oznaka mora biti dolga 1–400 znakov')
        rel=p.relative_to(self.files.root).as_posix()
        with self.db() as c:
            row=c.execute('SELECT text,populated FROM pages WHERE path=? AND page=?',(rel,page)).fetchone()
            if not row:raise ValueError('Stran še ni indeksirana')
            c.execute('UPDATE pages SET label=? WHERE path=? AND page=?',(label.strip(),rel,page))
            c.execute('DELETE FROM page_fts WHERE path=? AND page=?',(rel,page))
            if row['populated']:
                c.execute('INSERT INTO page_fts(path,page,text,label) VALUES (?,?,?,?)',(rel,page,row['text'],label.strip()))
        return {'path':rel,'page':page,'label':label.strip(),'stored_only_in_index':True}

    def activity(self,date_local:str,timezone_name:str='Europe/Ljubljana',limit:int=80):
        try:
            from datetime import date
            day=date.fromisoformat(date_local)
            zone=ZoneInfo(timezone_name)
        except Exception: raise ValueError('Uporabi datum YYYY-MM-DD in veljaven časovni pas') from None
        limit=min(max(limit,1),150)
        with self.db() as c:
            rows=c.execute('SELECT path,size,mtime_ns,indexed_utc FROM files ORDER BY mtime_ns DESC').fetchall()
            changes=c.execute('SELECT path,page,changed_utc FROM pages WHERE changed_utc IS NOT NULL ORDER BY changed_utc DESC').fetchall()
        mfiles=[{'path':r['path'],'modified_local':datetime.fromtimestamp(r['mtime_ns']/1e9,zone).isoformat()} for r in rows if datetime.fromtimestamp(r['mtime_ns']/1e9,zone).date()==day][:limit]
        pchanges=[{'path':r['path'],'page':r['page'],'detected_local':datetime.fromisoformat(r['changed_utc']).astimezone(zone).isoformat()} for r in changes if datetime.fromisoformat(r['changed_utc']).astimezone(zone).date()==day][:limit]
        return {'date':date_local,'timezone':timezone_name,'modified_files':mfiles,'detected_changed_pages':pchanges,
            'caution':'Datoteka je bila lahko prenesena ali ponovno izvožena; čas spremembe ni dokaz, katero snov si se učil. Spremembe posameznih strani se beležijo šele od prvega indeksiranja v1.1.'}
