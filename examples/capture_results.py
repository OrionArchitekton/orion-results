"""Synthetic installed-API walkthrough with selected backup and owned cleanup."""
import json
import os
from pathlib import Path
import shutil
from tempfile import mkdtemp
from orion_results import Session, Limits, Reference, ResultError, inspect_session, read


BACKUP_FILES = ('session.json', 'session.lock', 'results.sqlite3')


def copy_quiescent(source, target):
    # Caller holds the source writer, has just validated a snapshot and has no transaction.
    target.mkdir(mode=0o700)
    for name in BACKUP_FILES:
        fd=os.open(target/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with (source/name).open('rb') as origin, os.fdopen(fd,'wb') as dest:
            shutil.copyfileobj(origin,dest)
            dest.flush()
            os.fsync(dest.fileno())
    fd=os.open(target,os.O_DIRECTORY|os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def main():
    selected=Path(mkdtemp(prefix='result-capture-demo-'))
    root=selected/'records'
    backup=selected/'backup'
    large='synthetic large result|'*5000
    # On any exception leave this exclusively synthetic directory for inspection.
    with Session.create(root,limits=Limits(max_results=2)) as session:
        sid=session.session_id
        expected=Reference.from_text(sid,large)
        receipt=session.capture('large',large)
        session.capture('small','Hello from a synthetic result.')
        assert receipt.reference==expected
        assert read(root,expected)==large
        view=session.snapshot(budget_bytes=256)
        assert view.utf8_bytes==len(view.text.encode('utf-8'))<=256
        assert 'synthetic large result' not in view.text
        assert 'Hello' in session.snapshot(include_content=True).text
        try:
            session.capture('third','quota refused')
        except ResultError as error:
            assert error.code=='limit_exceeded' and error.outcome=='not_stored'
        else:
            raise AssertionError('quota was not enforced')
        assert session.snapshot(budget_bytes=256)==view
        backup.mkdir(mode=0o700)
        # snapshot validated the selected data; no capture occurs during this copy.
        copy_quiescent(root/sid,backup/sid)
        assert read(backup,expected)==large
        with Session.open(backup,sid) as restored:
            assert restored.snapshot(budget_bytes=256)==view
    assert inspect_session(root,sid).status=='complete'
    assert inspect_session(backup,sid).status=='complete'
    assert {p.name for p in selected.iterdir()}=={'records','backup'}
    for location in (root,backup):
        assert {p.name for p in location.iterdir()}=={sid}
        assert {p.name for p in (location/sid).iterdir()}==set(BACKUP_FILES)
    shutil.rmtree(selected)
    assert not selected.exists()
    print(json.dumps({'input_utf8_bytes':expected.utf8_bytes,'snapshot_utf8_bytes':view.utf8_bytes,
                      'unique':view.total_unique,'omitted':view.omitted_unique,
                      'exact_recovery':True,'backup_recovery':True,'quota_refused':True,
                      'cleanup_verified':True}))


if __name__=='__main__':
    main()
