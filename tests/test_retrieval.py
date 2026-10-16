import pytest
import pymupdf
from fe_gradiva.core import StudyFiles
from fe_gradiva.index import Index
from fe_gradiva.render import render_page, MAX_BYTES
from fe_gradiva.retrieval import exercise_pattern


def pdf(path, text=None, blank=False):
    d=pymupdf.open()
    p=d.new_page(width=595,height=842)
    if text: p.insert_text((40,50),text)
    if not blank: p.draw_line((50,80),(400,500),width=1.5)
    d.save(path)
    d.close()


def test_find_exercise_real_reference_and_source(tmp_path):
    root=tmp_path/'FE'; d=root/'1-semester'/'04_ELE'/'Vaje'; d.mkdir(parents=True)
    pdf(d/'Naloge.pdf','Naloga 1.1(b): Izracunaj naboj')
    idx=Index(StudyFiles(root),tmp_path/'index.sqlite'); idx.scan()
    matches=idx.find_exercise('1.1(b)',subject='ELE')['matches']
    assert len(matches)==1 and matches[0]['verified_reference'] is True
    assert matches[0]['page']==1 and '04_ELE' in matches[0]['path']
    assert idx.find_exercise('9.9',subject='ELE')['matches']==[]
    with pytest.raises(ValueError): idx.find_exercise('1.1.*')


def test_resolve_document_does_not_select_empty_template(tmp_path,monkeypatch):
    root=tmp_path/'FE'; d=root/'1-semester'/'04_ELE'; d.mkdir(parents=True)
    z=d/'Zvezek_ELE.pdf'
    d2=d/'Predavanja';d2.mkdir()
    d=pymupdf.open()
    for i in range(200):
        page=d.new_page(width=595,height=842)
        page.insert_text((560,822),str(i+1),fontsize=9)
    d.save(z);d.close()
    pdf(d2/'Zvezek.pdf','Elektrotehnika zvezek predavanja')
    monkeypatch.setattr('fe_gradiva.index.is_notebook',lambda name,pages:name.endswith('Zvezek_ELE.pdf'))
    monkeypatch.setattr('fe_gradiva.index.populated_by_ink',lambda page:False)
    idx=Index(StudyFiles(root),tmp_path/'idx.sqlite');idx.scan()
    candidates=idx.resolve_document('zvezek',subject='ELE')['candidates']
    assert candidates[0]['path'].endswith('Predavanja/Zvezek.pdf')
    assert 'empty_template' in candidates[-1]['match_reasons']


def test_cropped_image_is_valid_and_size_limited(tmp_path):
    p=tmp_path/'test.pdf';pdf(p,'Naloga 1.1(b)')
    full=render_page(p,1,zoom=3)
    crop=render_page(p,1,zoom=3,region=(0,0,.4,.4))
    assert full.data.startswith(b'\xff\xd8')
    assert crop.data.startswith(b'\xff\xd8')
    assert len(full.data)<MAX_BYTES
    assert crop.width<full.width
    with pytest.raises(ValueError):render_page(p,1,region=(.5,.2,.3,.9))
    with pytest.raises(ValueError):render_page(p,9)


def test_unlabelled_handwriting_not_presented_as_topic_match(tmp_path,monkeypatch):
    root=tmp_path/'FE';d=root/'1-semester'/'01_MAT'/'Zapiski';d.mkdir(parents=True)
    pdf(d/'Zvezek.pdf','1')
    monkeypatch.setattr('fe_gradiva.index.is_notebook',lambda name,pages:True)
    idx=Index(StudyFiles(root),tmp_path/'idx.sqlite');idx.scan()
    ctx=idx.study_context('vektorski produkt',subject='MAT')
    assert ctx['notes']==[]
    assert 'Unlabelled' in ctx['caution']
