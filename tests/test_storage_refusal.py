import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import pytest
import orion_results as api


def new_selected(tmp_path):
    root=tmp_path/'records'
    with api.Session.create(root) as s:
        reference=s.capture('key','retained').reference
    return root,root/reference.session_id,reference


@pytest.mark.parametrize('change',['table','trigger','view','counter-type','ordinal','digest','key-type','meta-extra'])
def test_unexpected_schema_and_invalid_rows_are_refused(tmp_path,change):
    root,directory,ref=new_selected(tmp_path)
    db=sqlite3.connect(directory/'results.sqlite3')
    if change=='table':
        db.execute('CREATE TABLE extra(value TEXT)')
    elif change=='trigger':
        db.execute('CREATE TRIGGER extra AFTER INSERT ON results BEGIN SELECT 1; END')
    elif change=='view':
        db.execute('CREATE VIEW extra AS SELECT * FROM results')
    elif change=='counter-type':
        db.execute("UPDATE session_meta SET committed_count='invalid'")
    elif change=='ordinal':
        db.execute('UPDATE results SET ordinal=2')
    elif change=='digest':
        db.execute("UPDATE results SET payload=?",(b'changed!',))
    elif change=='key-type':
        db.execute('UPDATE results SET first_key=?',(b'bytes-key',))
    else:
        db.execute('PRAGMA ignore_check_constraints=ON')
        db.execute('INSERT INTO session_meta SELECT 2,format_version,session_id,max_input_bytes,max_results,max_payload_bytes,inline_bytes,0,0,0 FROM session_meta')
    db.commit();db.close()
    before=hashlib.sha256((directory/'results.sqlite3').read_bytes()).hexdigest()
    report=api.inspect_session(root,ref.session_id)
    assert report.status=='corrupt' and not report.exhaustive
    with pytest.raises(api.ResultError):
        api.read(root,ref)
    with pytest.raises(api.ResultError):
        api.Session.open(root,ref.session_id)
    assert hashlib.sha256((directory/'results.sqlite3').read_bytes()).hexdigest()==before


@pytest.mark.parametrize('kind',['symlink','hardlink','mode','oversize','duplicate','nan','unknown','version','malformed'])
@pytest.mark.parametrize('name',['session.json','results.sqlite3','session.lock','results.sqlite3-journal'])
def test_unsafe_namespace_preserves_unrelated_sentinel(tmp_path,kind,name):
    if name!='session.json' and kind in ('duplicate','nan','unknown','version','malformed'):
        pytest.skip('marker-only encoding case')
    root,directory,ref=new_selected(tmp_path)
    p=directory/name
    sentinel=tmp_path/'sentinel'
    sentinel.write_bytes(b'outside synthetic sentinel')
    sentinel.chmod(0o600)
    if kind in ('symlink','hardlink'):
        p.unlink(missing_ok=True)
        if kind=='symlink':
            p.symlink_to(sentinel)
        else:
            os.link(sentinel,p)
    elif kind=='mode':
        if not p.exists():
            p.write_bytes(b'cold')
        p.chmod(0o644)
    elif kind=='oversize':
        if not p.exists():
            p.touch(mode=0o600)
        with p.open('r+b') as stream:
            stream.truncate(4097 if name=='session.json' else 134_217_729)
    else:
        data=json.loads(p.read_text())
        if kind=='duplicate':
            p.write_text(p.read_text()[:-1]+',"format_version":1}')
        elif kind=='nan':
            data['limits']['inline_bytes']=float('nan');p.write_text(json.dumps(data))
        elif kind=='unknown':
            data['unexpected']='value';p.write_text(json.dumps(data))
        elif kind=='version':
            data['format_version']=2;p.write_text(json.dumps(data))
        else:
            p.write_bytes(b'\xffbad json')
    before=sentinel.read_bytes()
    with pytest.raises(api.ResultError):
        api.read(root,ref)
    with pytest.raises(api.ResultError):
        api.Session.open(root,ref.session_id)
    assert sentinel.read_bytes()==before


@pytest.mark.parametrize('name',['session.json','results.sqlite3','session.lock'])
def test_live_handle_refuses_replaced_selected_inode(tmp_path,name):
    root=tmp_path/'records'
    with api.Session.create(root) as s:
        s.capture('key','retained')
        p=root/s.session_id/name
        replacement=tmp_path/'replacement'
        replacement.write_bytes(p.read_bytes());replacement.chmod(0o600)
        os.replace(replacement,p)
        with pytest.raises(api.ResultError) as failure:
            s.snapshot()
        assert failure.value.code=='unsafe_storage'
        with pytest.raises(api.ResultError) as failed_capture:
            s.capture('new','not stored')
        assert failed_capture.value.outcome=='not_stored'


def test_fifo_file_is_refused_without_blocking(tmp_path):
    root,directory,ref=new_selected(tmp_path)
    (directory/'session.json').unlink()
    os.mkfifo(directory/'session.json',0o600)
    script='''
import sys
from orion_results import read,ResultError
try: read(sys.argv[1],sys.argv[2])
except ResultError as e: assert e.code=='unsafe_storage'
else: raise AssertionError('FIFO accepted')
'''
    subprocess.run([sys.executable,'-I','-c',script,str(root),str(ref)],timeout=3,check=True)


@pytest.mark.parametrize('fault',['missing-lock','missing-db','bad-limit','bool-version','future-db','wal-header','wrong-role'])
def test_readonly_classification_never_repairs_or_creates(tmp_path,fault):
    root,directory,ref=new_selected(tmp_path)
    if fault in ('missing-lock','missing-db'):
        (directory/('session.lock' if fault=='missing-lock' else 'results.sqlite3')).unlink()
    elif fault in ('bad-limit','bool-version','wrong-role'):
        p=directory/'session.json';data=json.loads(p.read_text())
        if fault=='bad-limit': data['limits']['max_results']=True
        elif fault=='bool-version': data['format_version']=True
        else: data['role']='other-role'
        p.write_text(json.dumps(data))
    else:
        with (directory/'results.sqlite3').open('r+b') as stream:
            stream.seek(60 if fault=='future-db' else 18)
            stream.write((2).to_bytes(4,'big') if fault=='future-db' else b'\x02\x02')
    before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir()}
    with pytest.raises(api.ResultError): api.read(root,ref)
    if fault in ('missing-lock','missing-db','bad-limit'):
        report=api.inspect_session(root,ref.session_id)
        assert report.status==('corrupt' if fault=='bad-limit' else 'incomplete')
        assert not report.exhaustive
    else:
        with pytest.raises(api.ResultError) as e: api.inspect_session(root,ref.session_id)
        assert e.value.code=='unsupported_format'
    with pytest.raises(api.ResultError): api.Session.open(root,ref.session_id)
    after={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir()}
    assert before==after


def test_foreign_owner_observation_is_refused_simulated(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from orion_results import _storage
    root,directory,ref=new_selected(tmp_path)
    target=(directory/'results.sqlite3').stat().st_ino
    native=_storage.os.fstat
    def simulated_fstat(fd):
        actual=native(fd)
        if actual.st_ino==target:
            fields={name:getattr(actual,name) for name in ('st_mode','st_uid','st_nlink','st_size','st_ino','st_dev')}
            fields['st_uid']=os.geteuid()+1
            return SimpleNamespace(**fields)
        return actual
    with monkeypatch.context() as m:
        m.setattr(_storage.os,'fstat',simulated_fstat)
        with pytest.raises(api.ResultError) as failure: api.read(root,ref)
        assert failure.value.code=='unsafe_storage'


@pytest.mark.parametrize('location',['root','session','extra-entry'])
def test_untrusted_directory_or_unknown_entry_is_refused(tmp_path,location):
    root,directory,ref=new_selected(tmp_path)
    sentinel=tmp_path/'outside';sentinel.mkdir(mode=0o700)
    (sentinel/'unchanged').write_text('sentinel')
    if location=='root':
        moved=tmp_path/'saved-root';root.rename(moved);root.symlink_to(sentinel,target_is_directory=True)
    elif location=='session':
        directory.rename(tmp_path/'saved-session');directory.symlink_to(sentinel,target_is_directory=True)
    else:
        (directory/'unknown').write_text('unrecognized')
    with pytest.raises(api.ResultError): api.read(root,ref)
    with pytest.raises(api.ResultError): api.Session.open(root,ref.session_id)
    assert (sentinel/'unchanged').read_text()=='sentinel'
    assert sorted(p.name for p in sentinel.iterdir())==['unchanged']
