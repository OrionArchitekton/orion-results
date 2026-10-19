import json
import os
import subprocess
import sys
import pytest
import orion_results as api


def test_two_sessions_in_one_root_keep_both_sets_of_records(tmp_path):
    root=tmp_path/'records'
    code='''
import json,sys
from orion_results import Session
with Session.create(sys.argv[1]) as s:
    print(json.dumps(str(s.capture('same-key',sys.argv[2]).reference)))
'''
    processes=[subprocess.Popen([sys.executable,'-I','-c',code,str(root),text],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
               for text in ('first process','second process')]
    refs=[]
    for p in processes:
        out,err=p.communicate(timeout=20)
        assert p.returncode==0,err
        refs.append(api.Reference.parse(json.loads(out)))
    assert refs[0].session_id!=refs[1].session_id
    assert [api.read(root,r) for r in refs]==['first process','second process']


def test_second_writer_is_busy_until_real_owner_exits(tmp_path):
    root=tmp_path/'records'
    code='''
import json,sys
from orion_results import Session
s=Session.create(sys.argv[1])
print(json.dumps(str(s.capture('key','retained').reference)),flush=True)
sys.stdin.readline()
'''
    p=subprocess.Popen([sys.executable,'-I','-c',code,str(root)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        import select
        assert select.select([p.stdout],[],[],15)[0]
        ref=api.Reference.parse(json.loads(p.stdout.readline()))
        lock=root/ref.session_id/'session.lock'
        inode=lock.stat().st_ino
        for action in (api.Session.open,api.inspect_session):
            with pytest.raises(api.ResultError) as busy:
                action(root,ref.session_id)
            assert busy.value.code=='session_busy'
        assert api.read(root,ref)=='retained'
        p.terminate()
        p.wait(timeout=10)
        with api.Session.open(root,ref.session_id) as s:
            assert api.read(root,ref)=='retained'
            assert lock.stat().st_ino==inode
    finally:
        if p.poll() is None:
            p.kill();p.wait(timeout=10)


def test_forked_handle_cannot_write_or_unlock_parent(tmp_path):
    root=tmp_path/'records'
    with api.Session.create(root) as session:
        session.capture('key','retained')
        child=os.fork()
        if child==0:
            try:
                assert session._store.lfd is None
                assert session._store.dfd is None
                assert session._store.rfd is None
                assert session._store.db is None
                try:
                    session.capture('child','not allowed')
                except api.ResultError as e:
                    assert e.code=='wrong_process'
                else:
                    os._exit(2)
                session.close()
                os._exit(0)
            except BaseException:
                os._exit(3)
        _,status=os.waitpid(child,0)
        assert os.waitstatus_to_exitcode(status)==0
        code='''
import sys
from orion_results import Session,ResultError
try: Session.open(sys.argv[1],sys.argv[2])
except ResultError as e:
    assert e.code=='session_busy'
else: raise AssertionError('parent lock released')
'''
        subprocess.run([sys.executable,'-I','-c',code,str(root),session.session_id],check=True)
        assert session.snapshot().total_unique==1


@pytest.mark.parametrize('number,expected',[(11,'session_busy'),(13,'session_busy'),(5,'storage_unavailable')])
def test_only_lock_contention_errno_is_busy(tmp_path,monkeypatch,number,expected):
    import errno
    from orion_results import _storage
    root=tmp_path/'records'
    with api.Session.create(root) as s: sid=s.session_id
    def fail_lock(*args):
        raise OSError(number,'synthetic lock failure')
    with monkeypatch.context() as m:
        m.setattr(_storage.fcntl,'flock',fail_lock)
        with pytest.raises(api.ResultError) as failure:
            api.Session.open(root,sid)
    assert failure.value.code==expected
