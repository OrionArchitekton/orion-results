import json
import subprocess
import sys
import orion_results as api


def test_small_capture_recovers_after_reopen(tmp_path):
    root = tmp_path / "records"
    text = "Synthetic result: 42"
    with api.Session.create(root) as session:
        sid = session.session_id
        expected = api.Reference.from_text(sid, text)
        receipt = session.capture("first", text)
        assert receipt.reference == expected
        assert receipt.sha256 == expected.sha256
        assert receipt.utf8_bytes == len(text.encode())
        assert receipt.deduplicated is False
    consumer = subprocess.run(
        [sys.executable, "-I", "-c",
         "import json,sys; from orion_results import read; print(json.dumps(read(sys.argv[1],sys.argv[2])))",
         str(root), str(expected)], text=True, capture_output=True, check=True)
    assert json.loads(consumer.stdout) == text
    with api.Session.open(root, sid) as reopened:
        assert reopened.session_id == sid
        assert reopened.limits == api.Limits()
        assert api.read(root, expected) == text


def test_creation_is_private_under_permissive_umask(tmp_path):
    import os
    import stat
    script = '''
import os, pathlib, stat, sys
from orion_results import Session
os.umask(0o022)
root=pathlib.Path(sys.argv[1])
with Session.create(root) as s:
    s.capture('key','synthetic')
    assert stat.S_IMODE(root.stat().st_mode)==0o700
    assert stat.S_IMODE((root/s.session_id).stat().st_mode)==0o700
    for p in (root/s.session_id).iterdir():
        assert stat.S_IMODE(p.stat().st_mode)==0o600
'''
    subprocess.run([sys.executable, '-I', '-c', script, str(tmp_path/'private')], check=True)
    unsafe = tmp_path/'unsafe'
    unsafe.mkdir(mode=0o755)
    try:
        api.Session.create(unsafe)
        assert False, 'unsafe root accepted'
    except api.ResultError as e:
        assert e.code == 'unsafe_storage'
    assert stat.S_IMODE(unsafe.stat().st_mode) == 0o755


def test_import_and_install_create_no_state(tmp_path):
    subprocess.run([sys.executable, '-I', '-c', 'import orion_results'], cwd=tmp_path, check=True)
    assert list(tmp_path.iterdir()) == []


def test_invalid_input_publishes_nothing(tmp_path):
    import pytest
    from dataclasses import FrozenInstanceError
    with pytest.raises(api.ResultError):
        api.Session.create('relative')
    for limits in ({'max_results': True}, {'max_input_bytes':0}, {'inline_bytes':4001}):
        with pytest.raises(api.ResultError):
            api.Limits(**limits)
    with api.Session.create(tmp_path/'records') as s:
        for key,text in [('', 'ok'),('x'*257, 'ok'),('x', b'bad'),('x','\ud800'),(True,'ok')]:
            with pytest.raises(api.ResultError) as failure:
                s.capture(key,text)
            assert failure.value.outcome == 'not_stored'
        with pytest.raises((FrozenInstanceError, AttributeError)):
            s.limits.max_results = 8
        with pytest.raises(AttributeError):
            s.session_id = 'a'*32
        sid=s.session_id
    assert api.inspect_session(tmp_path/'records',sid).references == ()


def test_new_session_never_adopts_incomplete_initialization(tmp_path,monkeypatch):
    import pytest
    from orion_results import _storage
    root=tmp_path/'records'
    with monkeypatch.context() as m:
        def fail_sync(fd):
            raise OSError('synthetic initialization interruption')
        m.setattr(_storage.os,'fsync',fail_sync)
        with pytest.raises(api.ResultError) as failed:
            api.Session.create(root)
    sid=failed.value.session_id
    assert len(sid)==32
    before=set((root/sid).iterdir())
    inspection=api.inspect_session(root,sid)
    assert inspection.status=='incomplete'
    assert not inspection.exhaustive
    with pytest.raises(api.ResultError):
        api.Session.open(root,sid)
    assert set((root/sid).iterdir())==before
    with api.Session.create(root) as fresh:
        assert fresh.session_id!=sid
