"""MCP tools (read-only FE source; annotation writes only the isolated index)."""
from __future__ import annotations
import os
import threading
import time
from pathlib import Path
import pymupdf
from mcp.server.mcpserver import MCPServer, Image
from mcp.server.transport_security import TransportSecuritySettings
from . import __version__
from .core import StudyFiles, ALLOWED
from .index import Index
from .render import render_page

ROOT=StudyFiles(os.environ.get('FE_ROOT','/data/FE'))
INDEX=Index(ROOT,os.environ.get('FE_INDEX','/var/lib/fe-index/fe-index.sqlite3'))
CHANGE=threading.Event()
INTERVAL=max(30,int(os.environ.get('FE_RESCAN_SECONDS','180')))
mcp=MCPServer('FE Gradiva',version=__version__,instructions=(
    'Read-only access to FE documents and handwritten notes. Always use index_status first '
    'when indexing seems incomplete. Search via search_files or find_study_context; use '
    'populated_pages to avoid hundreds of blank notebook pages. If a page is handwritten, '
    'call view_pdf_page and read its image. Pages without labels cannot be reliably full-text searched. '
    'Use annotate_page ONLY after viewing a page; these tags go into a separate local index, never FE files. '
    'For activity report distinguish file modification from page changes and class work. '
    'Use resolve_document to disambiguate similarly named notebooks. Use find_exercise to verify original exercise values. '
    'Never assert an image-only exercise is verified unless its page is visually inspected. '
    'Always mention document path and page for study explanations.'
))

def _worker():
    while True:
        try:
            result=INDEX.scan()
            print('INDEX: ',result,flush=True)
        except Exception as e:
            print(f'INDEX ERROR: {type(e).__name__} {e}',flush=True)
        CHANGE.wait(INTERVAL)
        if CHANGE.is_set():
            CHANGE.clear()
            time.sleep(3) # Syncthing writes can be non-atomic in bursts.

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
    class Events(FileSystemEventHandler):
        def on_any_event(self,event):
            if '/.stversions/' not in event.src_path and not event.src_path.endswith('.tmp'):
                CHANGE.set()
    def _watch():
        obs=Observer()
        obs.schedule(Events(),str(ROOT.root),recursive=True)
        obs.daemon=True
        obs.start()
        return obs
except ImportError:
    def _watch():
        print('WARN: watchdog missing; periodic scan only',flush=True)

@mcp.tool()
def server_info() -> dict:
    """Current FE server capabilities, current folder availability and version."""
    return {'name':'FE Gradiva','version':__version__,'folder_available':ROOT.root.is_dir(),
            'read_only_FE':True,'extensions':sorted(ALLOWED),'index':INDEX.stats()}

@mcp.tool()
def index_status() -> dict:
    """See index progress, changed files and pending errors. First index can take minutes."""
    return INDEX.stats()

@mcp.tool()
def refresh_index() -> dict:
    """Request a background rescan of the local study folder, without editing source files."""
    CHANGE.set()
    return {'requested':True,'message':'Scan starts soon; call index_status to check progress.'}

@mcp.tool()
def list_files(directory:str='',recursive:bool=True,limit:int=100)->list[dict]:
    """List files in a FE directory, optionally recursive (up to 250 results)."""
    folder=ROOT.resolve(directory,is_dir=True)
    limit=max(1,min(int(limit),250))
    if recursive:
        result=[]
        for f in ROOT.iter_files():
            if f.is_relative_to(folder):
                result.append(ROOT.info(f))
                if len(result)>=limit:break
        return result
    result=[]
    for p in sorted(folder.iterdir(),key=lambda p:p.name.casefold()):
        if p.name.startswith('.') or p.is_symlink():continue
        if p.is_dir():
            result.append({'path':p.relative_to(ROOT.root).as_posix(),'type':'directory'})
        elif p.suffix.lower() in ALLOWED:
            try:result.append(ROOT.info(ROOT.resolve(p.relative_to(ROOT.root).as_posix())))
            except (ValueError,PermissionError,OSError):continue
        if len(result)>=limit:break
    return result

@mcp.tool()
def recent_files(limit:int=20)->list[dict]:
    """List most recently modified FE files; modification can mean sync or copy, not necessarily studying."""
    return [ROOT.info(p) for p in sorted(ROOT.iter_files(),key=lambda x:x.stat().st_mtime_ns,reverse=True)[:max(1,min(limit,100))]]

@mcp.tool()
def recent_activity(date_local:str,limit:int=80)->dict:
    """File edits on a chosen Slovenian date YYYY-MM-DD and page-level changes since v1.1 indexing."""
    return INDEX.activity(date_local,limit=limit)

@mcp.tool()
def file_info(path:str)->dict:
    """File metadata and PDF page count."""
    p=ROOT.resolve(path)
    data=ROOT.info(p)
    if p.suffix.lower()=='.pdf':
        with pymupdf.open(p) as pdf:
            data.update(pages=len(pdf),encrypted=bool(pdf.needs_pass))
    return data

@mcp.tool()
def populated_pages(path:str,limit:int=100,only_unlabeled:bool=False)->dict:
    """Return numbered PDF pages with actual writing/content, skipping blank grid pages."""
    return INDEX.populated_pages(path,limit=limit,only_unlabeled=only_unlabeled)

@mcp.tool()
def search_files(query:str,subject:str='',limit:int=20)->list[dict]:
    """Search fast SQLite FTS index (text and page labels), with source page/snippets. Subject e.g. 01_MAT."""
    return INDEX.search(query,subject=subject,limit=limit)

@mcp.tool()
def find_study_context(topic:str,subject:str='',limit:int=12)->dict:
    """Find topical student notes and professor material; unlabeled handwriting is marked uncertain."""
    return INDEX.study_context(topic,subject=subject,limit=limit)

@mcp.tool()
def resolve_document(query:str,subject:str='',kind_hint:str='',limit:int=8)->dict:
    """Find the correct current notebook or source; rank candidates and flag ambiguity or empty 1000-page templates."""
    return INDEX.resolve_document(query,subject,kind_hint,limit)

@mcp.tool()
def find_exercise(reference:str,subject:str='',limit:int=12)->dict:
    """Find exercise number in verifiable text and return page/source; scanned pages are NOT verified."""
    return INDEX.find_exercise(reference,subject,limit)

@mcp.tool()
def annotate_page(path:str,page:int,label:str)->dict:
    """After viewing a page, save topic keywords/description into a separate INDEX (never into FE files)."""
    return INDEX.annotate(path,page,label)

@mcp.tool()
def read_document(path:str,page:int=0,max_chars:int=16000)->dict:
    """Read selectable PDF text or text files. page=0 first pages, page=1 first page."""
    p=ROOT.resolve(path)
    max_chars=max(500,min(max_chars,30000))
    rel=p.relative_to(ROOT.root).as_posix()
    result={'path':rel,'requested_page':page}
    if p.suffix.lower()!='.pdf':
        with p.open('r',encoding='utf-8',errors='replace') as f: val=f.read(max_chars+1)
        return {**result,'text':val[:max_chars],'truncated':len(val)>max_chars}
    with pymupdf.open(p) as pdf:
        if pdf.needs_pass:raise ValueError('PDF je zaščiten z geslom')
        if not 0<=page<=len(pdf):raise ValueError('Neveljavna številka strani')
        if page:
            indices=[page-1]
        else:
            # For a huge graph notebook, retrieve only populated pages.
            populated=INDEX.populated_pages(rel,limit=25) if len(pdf)>=200 else None
            if populated and populated['notebook'] and populated['populated']:
                indices=[x['page']-1 for x in populated['populated']]
            else:
                indices=range(min(len(pdf),15))
        parts=[];used=0;truncated=False
        for i in indices:
            val=pdf[i].get_text(sort=True).strip()
            if not val or (val.isdigit() and len(val)<5):
                val='[Ni uporabnega strojno berljivega besedila. Uporabi view_pdf_page.]'
            part=f'[STRAN {i+1}]\n{val}\n'
            if used+len(part)>max_chars:
                truncated=True;break
            parts.append(part);used+=len(part)
        return {**result,'total_pages':len(pdf),'text':'\n'.join(parts),'truncated':truncated}

@mcp.tool()
def view_pdf_page(path:str,page:int,zoom:float=1.25,format:str='auto')->Image:
    """Display exact original PDF page as bounded JPEG/PNG, including handwritten calculations."""
    p=ROOT.resolve(path)
    if p.suffix.lower()!='.pdf':raise ValueError('PDF required')
    output=render_page(p,page,zoom,mode=format)
    return Image(data=output.data,format=output.fmt)

@mcp.tool()
def view_pdf_region(path:str,page:int,x0:float,y0:float,x1:float,y1:float,zoom:float=1.5)->Image:
    """Zoom into a PDF image subregion (normalized 0..1 coordinates) for equations and drawings."""
    p=ROOT.resolve(path)
    if p.suffix.lower()!='.pdf':raise ValueError('PDF required')
    output=render_page(p,page,zoom,region=(x0,y0,x1,y1))
    return Image(data=output.data,format=output.fmt)


def run():
    threading.Thread(target=_worker,name='fe-index-worker',daemon=True).start()
    global _OBSERVER
    _OBSERVER=_watch()
    security=TransportSecuritySettings(
        allowed_hosts=['fe-server','fe-server:*','localhost','localhost:*','127.0.0.1','127.0.0.1:*'],
        allowed_origins=['http://fe-server:8000','http://localhost:8000'])
    mcp.run(transport='streamable-http',host='0.0.0.0',port=8000,
            stateless_http=True,json_response=True,transport_security=security)

if __name__=='__main__':
    run()
