"""Regression checks for preprinted A4 grids and exact FE exercise titles."""
import pymupdf
from fe_gradiva.core import StudyFiles
from fe_gradiva.index import Index
from fe_gradiva.retrieval import document_score

def make_notebook(path, filled=(0,2), count=12):
    d=pymupdf.open()
    for i in range(count):
        p=d.new_page(width=595,height=842)
        for y in range(20,810,10): p.draw_line((20,y),(575,y),color=(.72,.72,.72),width=.35)
        for x in range(20,580,10): p.draw_line((x,20),(x,810),color=(.72,.72,.72),width=.35)
        p.insert_text((552,828),str(i+1),fontsize=9)
        if i in filled:
            p.insert_text((55,75),'Vektorji in Coulombova sila',fontsize=13)
            p.draw_line((50,150),(140,235),color=(0,0,.6),width=2)
    d.save(path);d.close()

def test_grid_classification_and_index_migration(tmp_path,monkeypatch):
    monkeypatch.setattr('fe_gradiva.index.is_notebook',lambda name,pages:True)
    root=tmp_path/'FE';root.mkdir();path=root/'Zvezek.pdf';make_notebook(path)
    idx=Index(StudyFiles(root),tmp_path/'idx.sqlite')
    idx.scan()
    assert [x['page'] for x in idx.populated_pages('Zvezek.pdf')['populated']]==[1,3]
    idx.annotate('Zvezek.pdf',1,'Vektorji')
    with idx.db() as db:
        db.execute('UPDATE pages SET populated=1')
        db.execute("UPDATE state SET value='old' WHERE key='occupancy_version'")
    assert idx.scan()['updated_files']==1
    assert idx.stats()['blank_pages']==10
    assert idx.populated_pages('Zvezek.pdf')['populated'][0]['label']=='Vektorji'

def test_numbered_exercises_require_header_and_correct_subpart(tmp_path):
    root=tmp_path/'FE';d=root/'04_ELE'/'Vaje';d.mkdir(parents=True)
    pdf=pymupdf.open();p=pdf.new_page()
    p.insert_text((40,60),'POGLAVJE: 1 VAJA: 1.1')
    p.insert_text((40,85),'a) sila')
    p.insert_text((40,110),'b) hitrost')
    p.insert_text((40,150),'POGLAVJE: 1 VAJA: 1.4')
    p.insert_text((40,175),'Spomni se 1.1(b), 8.11, 1.10')
    pdf.save(d/'Naloge.pdf');pdf.close()
    idx=Index(StudyFiles(root),tmp_path/'idx.sqlite');idx.scan()
    assert len(idx.find_exercise('1.1')['matches'])==1
    assert len(idx.find_exercise('1.1(b)')['matches'])==1
    assert idx.find_exercise('1.10')['matches']==[]
    assert idx.find_exercise('8.11')['matches']==[]

def test_unrelated_documents_are_not_ranked():
    score,reasons=document_score('04_ELE/Gradivo/FormuleOE1.pdf','Zvezek',100,100,'lecture_notes')
    assert score==0 and reasons==[]
