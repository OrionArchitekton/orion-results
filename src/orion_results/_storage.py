"""One private local SQLite implementation; no store adapters or automatic repair."""
from dataclasses import asdict
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import uuid
import weakref

from .models import Limits, Reference, ResultError, session_identity, utf8

APP_ID = 0x4F525352
DB_MAX = 134_217_728
DB_NAME = "results.sqlite3"
MARKER_MAX = 4096
SCHEMA = (
    "CREATE TABLE session_meta (singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
    "format_version INTEGER NOT NULL CHECK(format_version=1), session_id TEXT NOT NULL, "
    "max_input_bytes INTEGER NOT NULL, max_results INTEGER NOT NULL, "
    "max_payload_bytes INTEGER NOT NULL, inline_bytes INTEGER NOT NULL, "
    "committed_count INTEGER NOT NULL CHECK(committed_count>=0), "
    "committed_bytes INTEGER NOT NULL CHECK(committed_bytes>=0), "
    "next_ordinal INTEGER NOT NULL CHECK(next_ordinal>=0))",
    "CREATE TABLE results (sha256 TEXT PRIMARY KEY NOT NULL, utf8_bytes INTEGER NOT NULL "
    "CHECK(utf8_bytes>=0), first_key TEXT NOT NULL, ordinal INTEGER NOT NULL UNIQUE "
    "CHECK(ordinal>=0), payload BLOB NOT NULL)",
)
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
_stores = weakref.WeakSet()


def _after_fork_child():
    # close(), never LOCK_UN: flock open descriptions are shared with the parent.
    for store in tuple(_stores):
        store.close()


os.register_at_fork(after_in_child=_after_fork_child)


def platform_check():
    if sys.platform != "linux" or sys.implementation.name != "cpython" or sys.version_info < (3, 11):
        raise ResultError("unsupported_platform", "not_stored")
    if sqlite3.sqlite_version_info < (3, 35, 0) or not Path("/proc/self/fd").is_dir():
        raise ResultError("unsupported_platform", "not_stored")


def root_path(root):
    try:
        value = os.fspath(root)
    except TypeError:
        raise ResultError("invalid_input", "not_stored") from None
    if not isinstance(value, str) or not value or not os.path.isabs(value) or "\x00" in value:
        raise ResultError("invalid_input", "not_stored")
    if ".." in Path(value).parts:
        raise ResultError("invalid_input", "not_stored")
    return value


def check_stat(st, directory=False, maximum=None):
    good_type = stat.S_ISDIR(st.st_mode) if directory else stat.S_ISREG(st.st_mode)
    if (not good_type or st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) != (0o700 if directory else 0o600)
            or (not directory and st.st_nlink != 1)):
        raise ResultError("unsafe_storage")
    if maximum is not None and st.st_size > maximum:
        raise ResultError("unsafe_storage")


def open_directory(path, *, dir_fd=None):
    fd = os.open(path, DIR_FLAGS, dir_fd=dir_fd)
    try:
        check_stat(os.fstat(fd), directory=True)
        return fd
    except BaseException:
        os.close(fd)
        raise


def safe_file(dfd, name, maximum=None):
    fd = os.open(name, FILE_FLAGS, dir_fd=dfd)
    try:
        check_stat(os.fstat(fd), maximum=maximum)
        return fd
    except BaseException:
        os.close(fd)
        raise


def create_file(dfd, name):
    fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=dfd)
    try:
        check_stat(os.fstat(fd))
        return fd
    except BaseException:
        os.close(fd)
        raise


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate")
        result[key] = value
    return result


def marker(dfd, sid):
    fd = safe_file(dfd, "session.json", MARKER_MAX)
    try:
        raw = os.read(fd, MARKER_MAX + 1)
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(data, dict) or set(data) != {"role", "format_version", "session_id", "limits"}:
            raise ValueError()
        if data["role"] != "orion-results-session" or type(data["format_version"]) is not int or data["format_version"] != 1:
            raise ResultError("unsupported_format")
        if data["session_id"] != sid or not isinstance(data["limits"], dict) or set(data["limits"]) != set(asdict(Limits())):
            raise ValueError()
        try:
            return Limits(**data["limits"])
        except ResultError:
            raise ValueError() from None
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ResultError("corrupt") from None
    finally:
        os.close(fd)


def header(fd):
    raw = os.pread(fd, 100, 0)
    if len(raw) < 100:
        raise ResultError("recovery_required")
    if raw[:16] != b"SQLite format 3\x00":
        raise ResultError("corrupt")
    if (raw[18:20] != b"\x01\x01" or int.from_bytes(raw[60:64], "big") != 1
            or int.from_bytes(raw[68:72], "big") != APP_ID):
        raise ResultError("unsupported_format")


def namespace(dfd):
    allowed = {"session.json", "session.lock", DB_NAME, DB_NAME + "-journal"}
    entries = set(os.listdir(dfd))
    if entries - allowed:
        raise ResultError("unsafe_storage")
    if {"session.json", "session.lock", DB_NAME} - entries:
        raise ResultError("missing")
    for name in entries:
        fd = safe_file(dfd, name, MARKER_MAX if name == "session.json" else DB_MAX)
        os.close(fd)


def connect(dfd, writable=False, new=False):
    fd = safe_file(dfd, DB_NAME, DB_MAX)
    db = None
    try:
        before = os.fstat(fd)
        if not new:
            header(fd)
        uri = Path(f"/proc/self/fd/{dfd}/{DB_NAME}").as_uri() + ("?mode=rw" if writable else "?mode=ro")
        db = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=0)
        after = os.stat(DB_NAME, dir_fd=dfd, follow_symlinks=False)
        if (before.st_ino, before.st_dev) != (after.st_ino, after.st_dev):
            raise ResultError("unsafe_storage")
        check_stat(after, maximum=DB_MAX)
        db.execute("PRAGMA trusted_schema=OFF")
        db.execute("PRAGMA temp_store=MEMORY")
        if db.execute("PRAGMA temp_store").fetchone()[0] != 2:
            raise ResultError("unsupported_platform")
        if writable:
            if db.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
                raise ResultError("unsupported_format")
            db.execute("PRAGMA synchronous=EXTRA")
            if db.execute("PRAGMA synchronous").fetchone()[0] != 3:
                raise ResultError("unsupported_platform")
            size = db.execute("PRAGMA page_size").fetchone()[0]
            if db.execute(f"PRAGMA max_page_count={DB_MAX // size}").fetchone()[0] > DB_MAX // size:
                raise ResultError("unsafe_storage")
        else:
            db.execute("PRAGMA query_only=ON")
        return db
    except BaseException:
        if db is not None:
            db.close()
        raise
    finally:
        os.close(fd)


def validate(db, sid, limits):
    """Call only within a consistent transaction. Return transient rows, never cache."""
    schema = set(db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master"))
    expected_schema = {("table", "session_meta", "session_meta", SCHEMA[0]),
                       ("table", "results", "results", SCHEMA[1]),
                       ("index", "sqlite_autoindex_results_1", "results", None),
                       ("index", "sqlite_autoindex_results_2", "results", None)}
    if schema != expected_schema or db.execute("PRAGMA quick_check(1)").fetchone() != ("ok",):
        raise ResultError("corrupt")
    meta = db.execute("SELECT * FROM session_meta").fetchall()
    expected = (1, 1, sid, *asdict(limits).values())
    if (len(meta) != 1 or meta[0][:7] != expected or len(meta[0]) != 10
            or any(type(value) is not int for i, value in enumerate(meta[0]) if i != 2)):
        raise ResultError("corrupt")
    rows = db.execute("SELECT sha256,utf8_bytes,first_key,ordinal,payload FROM results ORDER BY ordinal").fetchall()
    total = 0
    for ordinal, (digest, size, key, order, payload) in enumerate(rows):
        if (type(payload) is not bytes or type(size) is not int or type(order) is not int
                or order != ordinal or len(payload) != size or size > limits.max_input_bytes
                or type(key) is not str or not 1 <= len(utf8(key)) <= 256
                or hashlib.sha256(payload).hexdigest() != digest):
            raise ResultError("corrupt")
        try:
            payload.decode("utf-8", errors="strict")
        except UnicodeError:
            raise ResultError("corrupt") from None
        total += size
    if (meta[0][7:] != (len(rows), total, len(rows)) or len(rows) > limits.max_results
            or total > limits.max_payload_bytes):
        raise ResultError("corrupt")
    return rows


def map_error(error, *, outcome="unavailable", sid=None):
    if isinstance(error, ResultError):
        return ResultError(error.code, outcome, session_id=sid)
    if isinstance(error, FileNotFoundError):
        return ResultError("missing", outcome, session_id=sid)
    if isinstance(error, OSError):
        import errno
        code = "unsafe_storage" if error.errno in (errno.ELOOP, errno.ENOTDIR) else "storage_unavailable"
        return ResultError(code, outcome, session_id=sid)
    if isinstance(error, sqlite3.DatabaseError):
        code = "recovery_required" if "readonly" in str(error).lower() else "corrupt"
        return ResultError(code, outcome, session_id=sid)
    return ResultError("storage_unavailable", outcome, session_id=sid)


class Store:
    def __init__(self, root, sid=None, limits=None, create=False, shared=False):
        self.rfd = self.dfd = self.lfd = None
        self.db = None
        self.sid = sid
        self.pid = os.getpid()
        self.shared = shared
        platform_check()
        path = root_path(root)
        self.root_path = path
        if not create:
            session_identity(sid)
        try:
            if create:
                try:
                    os.mkdir(path, 0o700)
                except FileExistsError:
                    pass
            self.rfd = open_directory(path)
            if create:
                sid = self.sid = uuid.uuid4().hex
                os.mkdir(sid, 0o700, dir_fd=self.rfd)
            self.dfd = open_directory(sid, dir_fd=self.rfd)
            if not create:
                self.limits = marker(self.dfd, sid)
            if create:
                fd = create_file(self.dfd, "session.json")
                try:
                    raw = json.dumps({"role": "orion-results-session", "format_version": 1,
                                      "session_id": sid, "limits": asdict(limits)}, sort_keys=True).encode()
                    if os.write(fd, raw) != len(raw):
                        raise OSError("short write")
                    os.fsync(fd)
                finally:
                    os.close(fd)
                self.lfd = create_file(self.dfd, "session.lock")
            else:
                self.lfd = safe_file(self.dfd, "session.lock")
            try:
                fcntl.flock(self.lfd, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
            except OSError as error:
                if error.errno in (errno.EACCES, errno.EAGAIN):
                    raise ResultError("session_busy") from None
                raise
            if create:
                fd = create_file(self.dfd, DB_NAME)
                os.close(fd)
                self.db = connect(self.dfd, writable=True, new=True)
                self.db.execute(f"PRAGMA application_id={APP_ID}")
                self.db.execute("PRAGMA user_version=1")
                self.db.execute("BEGIN IMMEDIATE")
                for sql in SCHEMA:
                    self.db.execute(sql)
                self.db.execute("INSERT INTO session_meta VALUES (1,1,?,?,?,?,?,0,0,0)",
                                (sid, *asdict(limits).values()))
                self.db.execute("COMMIT")
                os.fsync(self.dfd)
                os.fsync(self.rfd)
            self.limits = marker(self.dfd, sid)
            namespace(self.dfd)
            if self.db is None:
                self.db = connect(self.dfd, writable=not shared)
            self.identities = {name: self.identity(os.stat(name, dir_fd=self.dfd, follow_symlinks=False))
                               for name in ("session.json", "session.lock", DB_NAME)}
            self.rows()
            _stores.add(self)
        except (ResultError, OSError, sqlite3.DatabaseError) as error:
            self.close()
            mapped = map_error(error, outcome="not_stored" if not shared else "unavailable", sid=self.sid)
            mapped._observed_limits = getattr(self, "limits", None)
            raise mapped from None

    @staticmethod
    def identity(st):
        return st.st_dev, st.st_ino

    def guard(self):
        root_now = os.stat(self.root_path, follow_symlinks=False)
        session_now = os.stat(self.sid, dir_fd=self.rfd, follow_symlinks=False)
        check_stat(root_now, directory=True)
        check_stat(session_now, directory=True)
        if (self.identity(root_now) != self.identity(os.fstat(self.rfd))
                or self.identity(session_now) != self.identity(os.fstat(self.dfd))):
            raise ResultError("unsafe_storage")
        namespace(self.dfd)
        for name, expected in self.identities.items():
            if self.identity(os.stat(name, dir_fd=self.dfd, follow_symlinks=False)) != expected:
                raise ResultError("unsafe_storage")
        if marker(self.dfd, self.sid) != self.limits:
            raise ResultError("corrupt")

    def rows(self):
        self.guard()
        self.db.execute("BEGIN")
        try:
            return validate(self.db, self.sid, self.limits)
        finally:
            self.db.execute("ROLLBACK")

    def close(self):
        if self.db is not None:
            self.db.close()
            self.db = None
        for name in ("lfd", "dfd", "rfd"):
            fd = getattr(self, name)
            if fd is not None:
                os.close(fd)
                setattr(self, name, None)


def read_rows(root, reference):
    """Read without taking the writer lock; never opens writable or creates files."""
    rfd = dfd = db = None
    try:
        platform_check()
        rfd = open_directory(root_path(root))
        dfd = open_directory(reference.session_id, dir_fd=rfd)
        limits = marker(dfd, reference.session_id)
        namespace(dfd)
        db = connect(dfd)
        db.execute("BEGIN")
        rows = validate(db, reference.session_id, limits)
        for digest, size, key, ordinal, payload in rows:
            if digest == reference.sha256:
                if size != reference.utf8_bytes:
                    raise ResultError("corrupt")
                return payload.decode("utf-8")
        raise ResultError("missing")
    except (ResultError, OSError, sqlite3.DatabaseError) as error:
        raise map_error(error, sid=reference.session_id) from None
    finally:
        if db is not None:
            db.close()
        if dfd is not None:
            os.close(dfd)
        if rfd is not None:
            os.close(rfd)
