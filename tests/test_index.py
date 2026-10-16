import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import pymupdf
from fe_gradiva.core import StudyFiles, is_notebook
from fe_gradiva.index import Index


def build_pdf(path, pages=8, notebook=False):
    pdf=pymupdf.open()
    for i in range(pages):
        page=pdf.new_page(width=595,height=842)
        if notebook:
            # faint A4 squared paper + page numbering
            for y in range(20,800,20):
                page.draw_line((20,y),(575,y),color=(.84,.84,.84),width=.6)
            for x in range(20,575,20):
                page.draw_line((x,20),(x,800),color=(.84,.84,.84),width=.6)
            page.insert_text((550,820),str(i+1),fontsize=9)
            if i in (0,2):
                page.insert_text((45,90),'Odvodi in vektorji',fontsize=16,color=(.1,.1,.1))
                page.draw_line((60,150),(120,195),color=(.05,.05,.05),width=2)
        else:
            page.insert_text((40,80),'Odvod funkcije in pravilni vektorji',fontsize=15)
    pdf.save(path)
    pdf.close()

@pytest.fixture
def sample(tmp_path, monkeypatch):
    # Small test PDF exercises same logic as a 1000-page notebook, without slow fixture creation.
    monkeypatch.setattr('fe_gradiva.index.is_notebook', lambda path, pages: pages >= 8 and 'Zvezek' in path)
    root=tmp_path/'FE'; root.mkdir()
    (root/'1-semester'/'01_MAT'/'Gradivo').mkdir(parents=True)
    (root/'1-semester'/'01_MAT'/'Zapiski').mkdir(parents=True)
    p1=root/'1-semester'/'01_MAT'/'Gradivo'/'Uvod.pdf'
    p2=root/'1-semester'/'01_MAT'/'Zapiski'/'Zvezek.pdf'
    build_pdf(p1,4)
    build_pdf(p2,8,True)
    files=StudyFiles(root); index=Index(files,tmp_path/'db'/'index.sqlite')
    index.scan()
    return root,files,index,p1,p2


def test_scan_populated_and_blank(sample):
    root,files,index,p1,p2=sample
    r=index.populated_pages(str(p2.relative_to(root)),limit=50)
    assert r['notebook'] is True
    assert [v['page'] for v in r['populated']]==[1,3]
    status=index.stats()
    assert status['files']==2
    assert status['blank_pages']==6
    assert status['populated_pages']==6


def test_search_fast_and_source_types(sample):
    root,files,index,p1,p2=sample
    result=index.search('odvod',subject='01_MAT')
    assert result
    assert any(v['page']==1 for v in result)
    context=index.study_context('odvod',subject='01_MAT')
    assert context['professor_materials']
    assert context['notes']
    assert index.search('ne-obstaja-ne',subject='01_MAT')==[]


def test_annotate_persists_and_filter(sample):
    root,files,index,p1,p2=sample
    path=p2.relative_to(root).as_posix()
    assert index.annotate(path,1,'razlaga skalarnega produkta')['stored_only_in_index']
    assert any(v['page']==1 for v in index.search('skalarnega'))
    again=Index(files,index.db_path)
    assert any(v['page']==1 for v in again.search('skalarnega'))
    assert again.scan()['updated_files']==0
    assert again.populated_pages(path)['populated'][0]['label']=='razlaga skalarnega produkta'


def test_only_changed_pdf_reindexed(sample):
    root,files,index,p1,p2=sample
    assert index.scan()['updated_files']==0
    with pymupdf.open(p2) as doc:
        doc[2].insert_text((60,300),'Novo besedilo!',fontsize=16)
        doc.save(p2.with_suffix('.new.pdf'))
    p2.with_suffix('.new.pdf').replace(p2)
    assert index.scan()['updated_files']==1
    with index.db() as c:
        changed=[dict(x) for x in c.execute('SELECT page,changed_utc FROM pages WHERE path=? AND changed_utc IS NOT NULL',(p2.relative_to(root).as_posix(),))]
    assert [x['page'] for x in changed]==[3]
    assert any(x['page']==3 for x in index.search('novo'))


def test_insecure_paths_denied(sample,tmp_path):
    root,files,index,p1,p2=sample
    outside=tmp_path/'secret.txt';outside.write_text('private')
    (root/'evil.txt').symlink_to(outside)
    (root/'alias.pdf').symlink_to(p1)
    for path in ('evil.txt','alias.pdf','../secret.txt','../../etc/passwd','/.env'):
        with pytest.raises(ValueError): files.resolve(path)
    assert all('evil' not in x.relative_to(root).as_posix() for x in files.iter_files())


def test_activity_baseline_caution(sample):
    root,files,index,p1,p2=sample
    day=datetime.fromtimestamp(p1.stat().st_mtime,ZoneInfo('Europe/Ljubljana')).date().isoformat()
    a=index.activity(day)
    assert a['modified_files']
    assert not a['detected_changed_pages']
    assert 'šele od prvega' in a['caution']


def test_file_change_removal(sample):
    root,files,index,p1,p2=sample
    p1.unlink()
    index.scan()
    assert not any('Uvod.pdf' in x['path'] for x in index.search('odvod'))
    assert index.stats()['files']==1


def test_no_write_to_fe(sample):
    root,files,index,p1,p2=sample
    before={str(p): (p.stat().st_mtime_ns,p.stat().st_size) for p in files.iter_files()}
    index.annotate(p2.relative_to(root).as_posix(),1,'polje s številkami')
    index.scan()
    after={str(p): (p.stat().st_mtime_ns,p.stat().st_size) for p in files.iter_files()}
    assert before==after


def test_sql_query_is_safe(sample):
    _,_,idx,_,_=sample
    for q in ('" OR 1=1 --','*',"' UNION SELECT 1",'odvod'):
        assert isinstance(idx.search(q),list)
