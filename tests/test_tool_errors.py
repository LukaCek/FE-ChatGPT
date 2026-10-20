"""Error handling tests: missing files should NOT become opaque MCP failures."""
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from fe_gradiva.core import StudyFiles
from fe_gradiva.tool_errors import error_result, file_tool_errors


def test_missing_path_produces_actionable_structured_error():
    result=error_result(ValueError('Datoteka ali mapa ne obstaja'),
                        'Spletna-ucilnica/Ostali-predmeti/3625_MakerLab/Gradiva')
    assert result=={
        'ok':False, 'error':'not_found',
        'message':'Datoteka ali mapa ne obstaja. Najprej preveri pot z list_files.',
        'path':'Spletna-ucilnica/Ostali-predmeti/3625_MakerLab/Gradiva'
    }


def test_invalid_path_keeps_private_host_paths_out_of_responses():
    r=error_result(ValueError('/var/lib/fe-index/password=bad'),'/home/secret')
    assert r['error']=='invalid_request'
    assert 'password' not in str(r)
    assert len(error_result(ValueError('Neveljavna pot'),'x'*600)['path'])==0


def test_decorator_preserves_signature_and_valid_response(tmp_path):
    root=tmp_path/'FE';root.mkdir()
    files=StudyFiles(root)
    @file_tool_errors(path_arg='directory',list_output=True)
    def list_directory(directory:str='',recursive:bool=True)->list[dict]:
        folder=files.resolve(directory,is_dir=True)
        return [{'path':p.name} for p in folder.iterdir()]
    assert list_directory()==[]
    assert list_directory(directory='missing')==[{
        'ok':False,'error':'not_found',
        'message':'Datoteka ali mapa ne obstaja. Najprej preveri pot z list_files.',
        'path':'missing',
    }]
    assert list_directory('..')[0]['error']=='forbidden_path'
    assert list(inspect.signature(list_directory).parameters)==['directory','recursive']


def test_decorator_does_not_hide_unexpected_programming_bugs():
    @file_tool_errors()
    def read(path:str)->dict:
        raise RuntimeError('real programming bug')
    with pytest.raises(RuntimeError,match='real programming bug'):
        read('x.pdf')


def test_decorator_handles_missing_document_read():
    @file_tool_errors()
    def read(path:str)->dict:
        return {'path':path,'text':Path(path).read_text()}
    result=read('this/does/not/exist.txt')
    assert result['error']=='not_found'


def test_file_not_found_error_is_not_index_failure(tmp_path):
    root=tmp_path/'FE';root.mkdir()
    study=StudyFiles(root)
    with pytest.raises(ValueError,match='Datoteka ali mapa ne obstaja'):
        study.resolve('MakerLab/Gradiva',is_dir=True)
    assert error_result(ValueError('Datoteka ali mapa ne obstaja'))['error']=='not_found'
