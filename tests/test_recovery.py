import json
import os
import select
import sqlite3
import subprocess
import sys
import pytest
import orion_results as api


class FailSQL:
    def __init__(self, native, prefix, *, after=False, rollback=False):
        self.native=native
        self.prefix=prefix
        self.after=after
        self.rollback_failure=rollback
    def __getattr__(self,name):
        return getattr(self.native,name)
    def execute(self,sql,*args):
        if sql.startswith(self.prefix):
            if self.after:
                self.native.execute(sql,*args)
            raise sqlite3.OperationalError('synthetic transaction fault')
        if self.rollback_failure and sql=='ROLLBACK':
            raise OSError('synthetic rollback I/O failure')
        return self.native.execute(sql,*args)


@pytest.mark.parametrize('prefix,after,rollback,expected',[
    ('BEGIN IMMEDIATE',False,False,'not_stored'),
    ('SELECT * FROM session_meta',False,False,'not_stored'),
    ('INSERT INTO results',False,False,'not_stored'),
    ('UPDATE session_meta',False,False,'not_stored'),
    ('COMMIT',False,False,'commit_unknown'),
    ('COMMIT',True,False,'commit_unknown'),
    ('UPDATE session_meta',False,True,'commit_unknown')])
def test_failed_capture_returns_no_success_or_truncated_preview(tmp_path,prefix,after,rollback,expected):
    root=tmp_path/'records'
    original='😀'*1024
    with api.Session.create(root) as s:
        prior=s.capture('prior','kept').reference
        before=s.snapshot(budget_bytes=256)
        expected_ref=api.Reference.from_text(s.session_id,original)
        native=s._store.db
        s._store.db=FailSQL(native,prefix,after=after,rollback=rollback)
        try:
            with pytest.raises(api.ResultError) as failure:
                s.capture('PRIVATE-KEY',original)
            assert failure.value.outcome==expected
            assert 'PRIVATE-KEY' not in str(failure.value) and original not in str(failure.value)
            if expected=='commit_unknown':
                assert failure.value.code=='commit_unknown'
                assert failure.value.reference==expected_ref
                with pytest.raises(api.ResultError): s.snapshot()
                with pytest.raises(api.ResultError): s.capture('later','refused')
            else:
                s._store.db=native
                assert s.snapshot(budget_bytes=256)==before
                assert original=='😀'*1024
        finally:
            s._store.db=native
        sid=s.session_id
    assert api.read(root,prior)=='kept'
    report=api.inspect_session(root,sid)
    assert report.status=='complete'
    if after:
        assert api.read(root,expected_ref)==original
    else:
        with pytest.raises(api.ResultError) as missing: api.read(root,expected_ref)
        assert missing.value.code=='missing'


def test_interruption_after_real_commit_faults_handle(tmp_path):
    root=tmp_path/'records'
    with api.Session.create(root) as s:
        native=s._store.db
        class InterruptAfterCommit(FailSQL):
            def execute(self,sql,*args):
                value=self.native.execute(sql,*args)
                if sql=='COMMIT':
                    raise KeyboardInterrupt()
                return value
        s._store.db=InterruptAfterCommit(native,'COMMIT')
        expected=api.Reference.from_text(s.session_id,'uncertain')
        with pytest.raises(KeyboardInterrupt): s.capture('key','uncertain')
        s._store.db=native
        with pytest.raises(api.ResultError) as faulted: s.snapshot()
        assert faulted.value.code=='commit_unknown'
        with pytest.raises(api.ResultError): s.capture('later','must refuse')
    assert api.read(root,expected)=='uncertain'


def test_postcommit_readback_failure_is_unknown(tmp_path,monkeypatch):
    from orion_results import _storage
    root=tmp_path/'records'
    with api.Session.create(root) as s:
        expected=api.Reference.from_text(s.session_id,'committed')
        native=_storage.connect
        def fail_reader(dfd,writable=False,new=False):
            if not writable:
                raise OSError('synthetic postcommit read failure')
            return native(dfd,writable,new)
        with monkeypatch.context() as m:
            m.setattr(_storage,'connect',fail_reader)
            with pytest.raises(api.ResultError) as failed: s.capture('key','committed')
        assert failed.value.code==failed.value.outcome=='commit_unknown'
        assert failed.value.reference==expected
        with pytest.raises(api.ResultError): s.snapshot()
    assert api.read(root,expected)=='committed'


def test_native_page_ceiling_refuses_without_filling_host_disk(tmp_path):
    root=tmp_path/'records'
    with api.Session.create(root) as s:
        prior=s.capture('key','preserved').reference
        db=s._store.db
        pages=db.execute('PRAGMA page_count').fetchone()[0]
        assert db.execute(f'PRAGMA max_page_count={pages}').fetchone()[0]==pages
        observed=[]
        class ObserveFull:
            def __getattr__(self,name): return getattr(db,name)
            def execute(self,sql,*args):
                try: return db.execute(sql,*args)
                except sqlite3.DatabaseError as e:
                    observed.append(e.sqlite_errorname)
                    raise
        s._store.db=ObserveFull()
        with pytest.raises(api.ResultError) as failed: s.capture('large','x'*100000)
        s._store.db=db
        assert 'SQLITE_FULL' in observed
        assert failed.value.code=='write_failed' and failed.value.outcome=='not_stored'
        assert s.snapshot().total_unique==1
        assert api.read(root,prior)=='preserved'


BARRIER_WRITER = '''
import sys
from orion_results import Session
root,sid,stage,spill=sys.argv[1:]
s=Session.open(root,sid)
native=s._store.db
if spill=='yes': native.execute('PRAGMA cache_size=1')
class Barrier:
    def __getattr__(self,name): return getattr(native,name)
    def execute(self,sql,*args):
        if sql=='COMMIT' and stage=='pre':
            print('pre',flush=True)
            sys.stdin.readline()
        value=native.execute(sql,*args)
        if sql=='COMMIT' and stage=='post':
            print('post',flush=True)
            sys.stdin.readline()
        return value
s._store.db=Barrier()
s.capture('child', ('X'*1000000) if spill=='yes' else 'child-'+stage)
'''


def kill_owned_at_barrier(root,sid,stage,spill='no'):
    p=subprocess.Popen([sys.executable,'-I','-c',BARRIER_WRITER,str(root),sid,stage,spill],
                       stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        assert select.select([p.stdout],[],[],20)[0], 'owned writer barrier timed out'
        line=p.stdout.readline().strip()
        assert line==stage, p.stderr.read() if p.poll() is not None else line
        assert p.poll() is None
        p.kill()
        p.wait(timeout=10)
        assert p.returncode==-9
    finally:
        if p.poll() is None:
            p.kill();p.wait(timeout=10)


@pytest.mark.parametrize('stage',['pre','post'])
def test_no_return_after_commit_requires_exact_inspection(tmp_path,stage):
    root=tmp_path/'records'
    with api.Session.create(root) as s:
        prior=s.capture('prior','retained').reference
        sid=s.session_id
    expected=api.Reference.from_text(sid,'child-'+stage)
    kill_owned_at_barrier(root,sid,stage)
    inspection=api.inspect_session(root,sid)
    assert inspection.status=='complete'
    assert api.read(root,prior)=='retained'
    if stage=='post':
        assert expected in inspection.references
        assert api.read(root,expected)=='child-post'
    else:
        assert expected not in inspection.references
        with pytest.raises(api.ResultError) as e: api.read(root,expected)
        assert e.value.code=='missing'


def test_explicit_reopen_allows_native_journal_recovery_without_recapture(tmp_path):
    import hashlib
    root=tmp_path/'records'
    with api.Session.create(root) as s:
        prior=s.capture('prior','retained').reference
        sid=s.session_id
    expected=api.Reference.from_text(sid,'X'*1000000)
    kill_owned_at_barrier(root,sid,'pre','yes')
    directory=root/sid
    journal=directory/'results.sqlite3-journal'
    assert journal.exists() and journal.stat().st_size>512
    assert journal.read_bytes()[:8]!=b'\x00'*8
    before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir()}
    report=api.inspect_session(root,sid)
    assert report.status=='incomplete' and not report.exhaustive
    assert 'recovery_required' in report.findings
    with pytest.raises(api.ResultError) as pending: api.read(root,prior)
    assert pending.value.code=='recovery_required'
    assert before=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir()}
    with api.Session.open(root,sid) as recovered:
        assert recovered.snapshot().total_unique==1
        assert api.read(root,prior)=='retained'
        with pytest.raises(api.ResultError) as absent: api.read(root,expected)
        assert absent.value.code=='missing'
    assert api.inspect_session(root,sid).status=='complete'


def test_inspection_never_calls_a_missing_membership_row_healthy(tmp_path):
    root=tmp_path/'records'
    with api.Session.create(root) as s:
        first=s.capture('one','first').reference
        second=s.capture('two','second').reference
        path=root/s.session_id/'results.sqlite3'
        db=sqlite3.connect(path)
        db.execute('DELETE FROM results WHERE sha256=?',(second.sha256,))
        db.commit()
        assert db.execute('PRAGMA quick_check').fetchone()==('ok',)
        db.close()
        with pytest.raises(api.ResultError) as failed: s.snapshot()
        assert failed.value.code=='corrupt'
        with pytest.raises(api.ResultError): api.read(root,first)
        sid=s.session_id
    report=api.inspect_session(root,sid)
    assert report.status=='corrupt' and not report.exhaustive
    assert report.references==()


def test_cold_journal_is_judged_by_readonly_sqlite(tmp_path):
    root=tmp_path/'records'
    with api.Session.create(root) as s:
        ref=s.capture('key','retained').reference
    journal=root/ref.session_id/'results.sqlite3-journal'
    fd=os.open(journal,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'wb') as f: f.write(b'\x00'*512)
    before=journal.read_bytes()
    assert api.inspect_session(root,ref.session_id).status=='complete'
    assert api.read(root,ref)=='retained'
    assert journal.exists() and journal.read_bytes()==before
