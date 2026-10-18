import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
import orion_results as api


def fingerprints(directory):
    return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir()}


def test_quiescent_session_restores_under_an_explicit_new_private_root(tmp_path):
    root=tmp_path/'original'
    restored=tmp_path/'restored'
    unrelated=tmp_path/'unrelated'
    unrelated.write_text('sentinel')
    with api.Session.create(root) as s:
        refs=[s.capture('key',text).reference for text in ('small','日本\x00'*2000)]
        before=s.snapshot()
        assert not s._store.db.in_transaction
        source=root/s.session_id
        restored.mkdir(mode=0o700)
        target=restored/s.session_id
        target.mkdir(mode=0o700)
        for name in ('session.json','session.lock','results.sqlite3'):
            fd=os.open(target/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with (source/name).open('rb') as origin,os.fdopen(fd,'wb') as dest:
                shutil.copyfileobj(origin,dest)
                dest.flush();os.fsync(dest.fileno())
        assert (target/'session.lock').stat().st_ino!=(source/'session.lock').stat().st_ino
        assert [api.read(restored,r) for r in refs]==['small','日本\x00'*2000]
        with api.Session.open(restored,s.session_id) as copy:
            assert copy.snapshot()==before
            assert copy.limits==s.limits
        assert s.snapshot()==before
    assert api.inspect_session(restored,refs[0].session_id).status=='complete'
    assert unrelated.read_text()=='sentinel'


def test_uninstall_preserves_captured_data_and_reinstall_reads_it(tmp_path):
    root=tmp_path/'data'
    with api.Session.create(root) as s:
        ref=s.capture('key','survives uninstall').reference
    selected=root/ref.session_id
    before=fingerprints(selected)
    direct=json.loads(importlib.metadata.distribution('orion-results').read_text('direct_url.json'))
    parsed=urllib.parse.urlsplit(direct['url'])
    assert parsed.scheme=='file' and not parsed.netloc
    wheel=Path(urllib.request.url2pathname(parsed.path))
    assert wheel.is_file() and wheel.suffix=='.whl'
    try:
        subprocess.run([sys.executable,'-m','pip','uninstall','-y','orion-results'],check=True,capture_output=True)
        subprocess.run([sys.executable,'-I','-c',"import importlib.util; assert importlib.util.find_spec('orion_results') is None"],check=True)
        assert fingerprints(selected)==before
    finally:
        subprocess.run([sys.executable,'-m','pip','install','--no-index','--no-deps','--force-reinstall',str(wheel)],check=True,capture_output=True)
    restored=subprocess.run([sys.executable,'-I','-c','import sys;from orion_results import read;print(read(sys.argv[1],sys.argv[2]))',str(root),str(ref)],capture_output=True,text=True,check=True)
    assert restored.stdout.strip()=='survives uninstall'
    assert fingerprints(selected)==before


def installed_archives():
    direct=json.loads(importlib.metadata.distribution('orion-results').read_text('direct_url.json'))
    wheel=Path(urllib.request.url2pathname(urllib.parse.urlsplit(direct['url']).path))
    sdists=list(wheel.parent.glob('orion_results-0.1.0.tar.gz'))
    assert len(sdists)==1
    return wheel,sdists[0]


def test_wheel_sdist_have_only_the_reviewed_export():
    import tarfile
    import zipfile
    wheel,sdist=installed_archives()
    required={'README.md','LICENSE','NOTICE','specs/orion-results-core-spec.md',
              'docs/runbooks/result-capture.md','examples/capture_results.py',
              'docs/releases/verification-0.1.0.json'}
    with zipfile.ZipFile(wheel) as z:
        names=set(z.namelist())
        for relative in required:
            assert any(name.endswith('/share/orion-results/'+relative) for name in names),relative
        metadata=z.read('orion_results-0.1.0.dist-info/METADATA').decode()
        assert 'Requires-Dist:' not in metadata
        entries={name:z.read(name) for name in names if not name.endswith('/')}
    with tarfile.open(sdist) as archive:
        members=archive.getmembers()
        assert all(m.isfile() or m.isdir() for m in members)
        for relative in required:
            assert any(m.name.endswith('/'+relative) for m in members),relative
        for m in members:
            if m.isfile(): entries[m.name]=archive.extractfile(m).read()
    for name,data in entries.items():
        assert not any(part in name.split('/') for part in ('.private','.git','.claude','.codex','__pycache__','notes'))
        assert not name.endswith(('.sqlite3','.sqlite3-journal','.pyc'))
        if name.endswith(('.py','.md','.json','.toml','.yml','.in','.txt')):
            assert (b'/home/' + b'orion/') not in data,name
            assert (b'100.' + b'80.') not in data,name
    assert b'PRIVATE REVIEW DRAFT' in next(value for name,value in entries.items() if name.endswith('/LICENSE'))
