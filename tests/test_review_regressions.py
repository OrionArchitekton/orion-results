"""Installed-API regressions for the initial public release review."""
from pathlib import Path
import os
import stat
import select
import sqlite3
import subprocess
import sys
import threading
import pytest
import orion_results as api


@pytest.mark.parametrize('suffix', ['/', '//', '/./'])
def test_root_suffix_cannot_hide_a_final_symlink(tmp_path, suffix):
    target = tmp_path / 'selected'
    with api.Session.create(target) as session:
        reference = session.capture('key', 'original bytes').reference
    link = tmp_path / 'link'
    link.symlink_to(target, target_is_directory=True)
    before = sorted(p.relative_to(target) for p in target.rglob('*'))
    selected = str(link) + suffix
    with pytest.raises(api.ResultError) as error:
        with api.Session.create(selected):
            pass
    assert error.value.code == 'unsafe_storage'
    with pytest.raises(api.ResultError) as error:
        with api.Session.open(selected, reference.session_id):
            pass
    assert error.value.code == 'unsafe_storage'
    with pytest.raises(api.ResultError) as error:
        api.read(selected, reference)
    assert error.value.code == 'unsafe_storage'
    assert api.inspect_session(selected, reference.session_id).findings == ('unsafe_storage',)
    assert sorted(p.relative_to(target) for p in target.rglob('*')) == before
    assert api.read(target, reference) == 'original bytes'


def test_interrupted_creation_releases_descriptors_and_writer_lease(tmp_path, monkeypatch):
    from orion_results import _storage
    root = tmp_path / 'selected'
    native = os.fsync
    interrupted = False

    def interrupt_directory_sync(fd):
        nonlocal interrupted
        if not interrupted and stat.S_ISDIR(os.fstat(fd).st_mode):
            interrupted = True
            raise KeyboardInterrupt('synthetic directory-sync interruption')
        return native(fd)

    with monkeypatch.context() as patch:
        patch.setattr(_storage.os, 'fsync', interrupt_directory_sync)
        with pytest.raises(KeyboardInterrupt) as interruption:
            api.Session.create(root)
    assert interruption.value.args == ('synthetic directory-sync interruption',)
    leaked = []
    for descriptor in Path('/proc/self/fd').iterdir():
        try:
            selected = os.readlink(descriptor)
        except FileNotFoundError:
            continue
        if selected == str(root) or selected.startswith(str(root) + '/'):
            leaked.append(selected)
    assert leaked == []
    [residue] = list(root.iterdir())
    with api.Session.open(root, residue.name) as recovered:
        assert recovered.snapshot().total_unique == 0


@pytest.mark.parametrize('rollback_fails', [False, True])
def test_precommit_interruption_rolls_back_or_faults_handle(tmp_path, rollback_fails):
    root = tmp_path / 'selected'
    with api.Session.create(root) as session:
        prior = session.capture('prior', 'retained').reference
        before = session.snapshot()
        native = session._store.db

        class InterruptedWrite:
            def __getattr__(self, name):
                return getattr(native, name)

            def execute(self, sql, *args):
                if sql.startswith('INSERT INTO results'):
                    raise KeyboardInterrupt('synthetic precommit interruption')
                if sql == 'ROLLBACK' and rollback_fails:
                    raise OSError('synthetic rollback failure')
                return native.execute(sql, *args)

        session._store.db = InterruptedWrite()
        try:
            with pytest.raises(KeyboardInterrupt) as interruption:
                session.capture('later', 'interrupted text')
            assert interruption.value.args == ('synthetic precommit interruption',)
        finally:
            session._store.db = native
        if rollback_fails:
            with pytest.raises(api.ResultError) as error:
                session.snapshot()
            assert error.value.code == 'commit_unknown'
            with pytest.raises(api.ResultError) as error:
                session.capture('refused', 'no further capture')
            assert error.value.code == 'commit_unknown'
        else:
            assert not native.in_transaction
            assert session.snapshot() == before
            session.capture('after', 'a new complete result')
    assert api.read(root, prior) == 'retained'


def test_optional_journal_disappearing_at_native_commit_is_not_missing(tmp_path, monkeypatch):
    from orion_results import _storage
    root = tmp_path / 'selected'
    with api.Session.create(root) as session:
        prior = session.capture('prior', 'retained').reference
    process = subprocess.Popen([sys.executable, '-I', '-c', '''
import sys
from orion_results import Session
with Session.open(sys.argv[1],sys.argv[2]) as session:
    native=session._store.db
    class CommitBarrier:
        def __getattr__(self,name): return getattr(native,name)
        def execute(self,sql,*args):
            if sql=='COMMIT':
                print('ready',flush=True)
                assert sys.stdin.readline()=='commit\\n'
            return native.execute(sql,*args)
    session._store.db=CommitBarrier()
    receipt=session.capture('later','complete later result')
    print(str(receipt.reference),flush=True)
''', str(root), prior.session_id], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True)
    try:
        assert select.select([process.stdout], [], [], 10)[0]
        assert process.stdout.readline().strip() == 'ready'
        directory = root / prior.session_id
        assert (directory / 'results.sqlite3-journal').exists()
        inode = directory.stat().st_ino
        native_list = os.listdir
        committed = []

        def list_then_commit(selected):
            entries = native_list(selected)
            if isinstance(selected, int) and os.fstat(selected).st_ino == inode and not committed:
                assert 'results.sqlite3-journal' in entries
                output, errors = process.communicate('commit\n', timeout=10)
                assert process.returncode == 0, errors
                committed.append(api.Reference.parse(output.strip()))
                assert not (directory / 'results.sqlite3-journal').exists()
            return entries

        monkeypatch.setattr(_storage.os, 'listdir', list_then_commit)
        assert api.read(root, prior) == 'retained'
        assert len(committed) == 1
        assert api.read(root, committed[0]) == 'complete later result'
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    assert api.inspect_session(root, prior.session_id).status == 'complete'


def test_overlapping_reader_cannot_release_the_writers_sqlite_lock(tmp_path, monkeypatch):
    from orion_results import _storage
    root = tmp_path / 'selected'
    with api.Session.create(root) as session:
        prior = session.capture('prior', 'retained').reference
        native = session._store.db
        validate = _storage.validate
        observed = []
        exercised = False

        def reader():
            try:
                observed.append(('read', api.read(root, prior)))
            except api.ResultError as error:
                observed.append(('error', error.code))

        def inspect_real_reserved_lock(db, sid, limits):
            nonlocal exercised
            rows = validate(db, sid, limits)
            if db is native and not exercised:
                exercised = True
                thread = threading.Thread(target=reader)
                thread.start()
                thread.join(timeout=5)
                assert not thread.is_alive()
                probe = subprocess.run([sys.executable, '-I', '-c', '''
import sqlite3,sys
db=sqlite3.connect(sys.argv[1],isolation_level=None,timeout=0)
try:
    db.execute('BEGIN IMMEDIATE')
except sqlite3.OperationalError as error:
    assert error.sqlite_errorcode & 255 == sqlite3.SQLITE_BUSY
    print('busy')
else:
    db.execute('ROLLBACK')
    print('acquired')
finally:
    db.close()
''', str(root / sid / 'results.sqlite3')], capture_output=True, text=True, timeout=5, check=True)
                assert probe.stdout.strip() == 'busy', 'an overlapping read released the native writer lock'
                assert observed == [('error', 'storage_busy')]
            return rows

        monkeypatch.setattr(_storage, 'validate', inspect_real_reserved_lock)
        later = session.capture('later', 'complete second result').reference
        assert exercised
        assert api.read(root, later) == 'complete second result'
    assert api.read(root, prior) == 'retained'


@pytest.mark.parametrize('operation', ['snapshot', 'inspection'])
def test_read_error_survives_sqlite_ending_its_transaction(tmp_path, monkeypatch, operation):
    from orion_results import _storage
    root = tmp_path / 'selected'
    session = api.Session.create(root)
    prior = session.capture('prior', 'retained').reference
    if operation == 'inspection':
        session.close()

    def io_error_after_auto_rollback(db, *args):
        db.execute('ROLLBACK')
        error = sqlite3.OperationalError('synthetic SQLite read I/O failure')
        error.sqlite_errorcode = sqlite3.SQLITE_IOERR
        raise error

    try:
        with monkeypatch.context() as patch:
            patch.setattr(_storage, 'validate', io_error_after_auto_rollback)
            with pytest.raises(api.ResultError) as error:
                if operation == 'snapshot':
                    session.snapshot()
                else:
                    api.inspect_session(root, prior.session_id)
            assert error.value.code == 'storage_unavailable'
    finally:
        session.close()
    assert api.read(root, prior) == 'retained'


def test_snapshot_preserves_validation_error_when_rollback_also_fails(tmp_path, monkeypatch):
    from orion_results import _storage
    root = tmp_path / 'selected'
    with api.Session.create(root) as session:
        session.capture('prior', 'retained')
        native = session._store.db

        class RollbackFailure:
            def __getattr__(self, name):
                return getattr(native, name)

            def execute(self, sql, *args):
                if sql == 'ROLLBACK':
                    raise OSError('synthetic rollback failure')
                return native.execute(sql, *args)

        def corrupt(*args):
            raise api.ResultError('corrupt')

        try:
            session._store.db = RollbackFailure()
            with monkeypatch.context() as patch:
                patch.setattr(_storage, 'validate', corrupt)
                with pytest.raises(api.ResultError) as error:
                    session.snapshot()
                assert error.value.code == 'corrupt'
                with pytest.raises(api.ResultError) as busy:
                    session.snapshot()
                assert busy.value.code == 'storage_busy'
                with pytest.raises(api.ResultError) as busy:
                    session.capture('later', 'refused until the handle is closed')
                assert busy.value.code == 'storage_busy'
        finally:
            session._store.db = native
            if native.in_transaction:
                native.execute('ROLLBACK')


@pytest.mark.parametrize('required', ['session.json', 'session.lock', 'results.sqlite3'])
def test_disappearing_required_entry_is_still_missing(tmp_path, monkeypatch, required):
    from orion_results import _storage
    root = tmp_path / 'selected'
    with api.Session.create(root) as session:
        reference = session.capture('prior', 'retained').reference
    directory = root / reference.session_id
    inode = directory.stat().st_ino
    native = os.listdir
    removed = False

    def list_then_remove(selected):
        nonlocal removed
        entries = native(selected)
        if isinstance(selected, int) and os.fstat(selected).st_ino == inode and not removed:
            (directory / required).unlink()
            removed = True
        return entries

    monkeypatch.setattr(_storage.os, 'listdir', list_then_remove)
    with pytest.raises(api.ResultError) as error:
        api.read(root, reference)
    assert removed and error.value.code == 'missing'


def test_child_can_read_after_fork_while_another_thread_owns_operation(tmp_path, monkeypatch):
    from orion_results import _storage
    root = tmp_path / 'selected'
    with api.Session.create(root) as session:
        reference = session.capture('prior', 'retained').reference
    entered = threading.Event()
    resume = threading.Event()
    native = _storage.marker
    parent = os.getpid()
    observed = []

    def pause_parent(*args):
        value = native(*args)
        if os.getpid() == parent:
            entered.set()
            assert resume.wait(10)
        return value

    def reader():
        observed.append(api.read(root, reference))

    monkeypatch.setattr(_storage, 'marker', pause_parent)
    thread = threading.Thread(target=reader)
    thread.start()
    try:
        assert entered.wait(5)
        child = os.fork()
        if child == 0:
            try:
                os._exit(0 if api.read(root, reference) == 'retained' else 2)
            except BaseException:
                os._exit(3)
        resume.set()
        _, status = os.waitpid(child, 0)
        assert os.waitstatus_to_exitcode(status) == 0
    finally:
        resume.set()
        thread.join(timeout=10)
    assert not thread.is_alive() and observed == ['retained']


def test_unknown_writer_is_closed_before_an_exact_read_can_open_raw_database(tmp_path):
    root = tmp_path / 'selected'
    with api.Session.create(root) as session:
        reference = session.capture('prior', 'retained').reference
        native = session._store.db

        class UncertainRollback:
            def __getattr__(self, name):
                return getattr(native, name)

            def execute(self, sql, *args):
                if sql.startswith('INSERT INTO results'):
                    raise OSError('synthetic precommit I/O failure')
                if sql == 'ROLLBACK':
                    raise OSError('synthetic rollback failure')
                return native.execute(sql, *args)

        session._store.db = UncertainRollback()
        try:
            with pytest.raises(api.ResultError) as error:
                session.capture('later', 'uncertain input')
            assert error.value.code == 'commit_unknown'
        finally:
            session._store.db = native
        assert native.in_transaction
        with pytest.raises(api.ResultError) as error:
            api.read(root, reference)
        assert error.value.code == 'storage_busy'
        probe = subprocess.run([sys.executable, '-I', '-c', '''
import sqlite3,sys
db=sqlite3.connect(sys.argv[1],isolation_level=None,timeout=0)
try:
    db.execute('BEGIN IMMEDIATE')
except sqlite3.OperationalError as error:
    assert error.sqlite_errorcode & 255 == sqlite3.SQLITE_BUSY
else:
    raise AssertionError('unknown writer lost its native lock')
finally:
    db.close()
''', str(root / session.session_id / 'results.sqlite3')], capture_output=True, text=True, timeout=5)
        assert probe.returncode == 0, probe.stderr
    assert api.read(root, reference) == 'retained'


def test_interruption_after_read_begin_cleans_up_the_transaction(tmp_path):
    root = tmp_path / 'selected'
    with api.Session.create(root) as session:
        session.capture('prior', 'retained')
        before = session.snapshot()
        native = session._store.db

        class InterruptedBegin:
            def __getattr__(self, name):
                return getattr(native, name)

            def execute(self, sql, *args):
                result = native.execute(sql, *args)
                if sql == 'BEGIN':
                    raise KeyboardInterrupt('synthetic read-BEGIN interruption')
                return result

        try:
            session._store.db = InterruptedBegin()
            with pytest.raises(KeyboardInterrupt):
                session.snapshot()
        finally:
            session._store.db = native
        assert not native.in_transaction
        assert session.snapshot() == before
