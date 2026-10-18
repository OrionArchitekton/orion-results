"""Explicit capture session lifecycle and exact-reference recovery."""
from dataclasses import asdict
import os
import sqlite3

from . import _storage
from .models import CaptureReceipt, Inspection, Limits, Reference, ResultError, utf8


class Session:
    def __init__(self, store):
        self._store = store
        self._pid = os.getpid()
        self._closed = False
        self._faulted = False

    @classmethod
    def create(cls, root, *, limits=Limits()):
        if type(limits) is not Limits:
            raise ResultError("invalid_input", "not_stored")
        return cls(_storage.Store(root, limits=limits, create=True))

    @classmethod
    def open(cls, root, session_id):
        return cls(_storage.Store(root, session_id))

    @property
    def session_id(self):
        return self._store.sid

    @property
    def limits(self):
        return self._store.limits

    def _check(self):
        if os.getpid() != self._pid:
            raise ResultError("wrong_process", "not_stored", session_id=self.session_id)
        if self._closed:
            raise ResultError("closed_session", "not_stored", session_id=self.session_id)
        if self._faulted:
            raise ResultError("commit_unknown", "commit_unknown", session_id=self.session_id)

    def capture(self, key, text):
        self._check()
        key_raw, payload = utf8(key), utf8(text)
        if not 1 <= len(key_raw) <= 256:
            raise ResultError("invalid_input", "not_stored", session_id=self.session_id)
        if len(payload) > self.limits.max_input_bytes:
            raise ResultError("limit_exceeded", "not_stored", session_id=self.session_id)
        ref = Reference.from_text(self.session_id, text)
        db = self._store.db
        commit_attempted = False
        try:
            self._store.guard()
            db.execute("BEGIN IMMEDIATE")
            rows = _storage.validate(db, self.session_id, self.limits)
            duplicate = False
            for digest, size, first_key, ordinal, old_payload in rows:
                if digest == ref.sha256:
                    if size != ref.utf8_bytes or payload != old_payload:
                        raise ResultError("content_conflict")
                    duplicate = True
                    break
            if not duplicate:
                total = sum(row[1] for row in rows)
                if len(rows) >= self.limits.max_results or total + len(payload) > self.limits.max_payload_bytes:
                    raise ResultError("limit_exceeded")
                db.execute("INSERT INTO results VALUES (?,?,?,?,?)", (ref.sha256, len(payload), key, len(rows), payload))
                db.execute("UPDATE session_meta SET committed_count=committed_count+1, committed_bytes=committed_bytes+?, next_ordinal=next_ordinal+1", (len(payload),))
            commit_attempted = True
            db.execute("COMMIT")
            self._store.guard()
            # A distinct read-only connection verifies application counters and exact bytes.
            verify = _storage.connect(self._store.dfd)
            try:
                verify.execute("BEGIN")
                checked = _storage.validate(verify, self.session_id, self.limits)
                if not any(row[0] == ref.sha256 and row[1] == ref.utf8_bytes and row[4] == payload for row in checked):
                    raise ResultError("corrupt")
            finally:
                verify.close()
            return CaptureReceipt(ref, ref.utf8_bytes, ref.sha256, duplicate)
        except (ResultError, OSError, sqlite3.DatabaseError) as error:
            unknown = commit_attempted
            try:
                if db.in_transaction:
                    db.execute("ROLLBACK")
            except sqlite3.DatabaseError:
                unknown = True
            if unknown:
                self._faulted = True
                raise ResultError("commit_unknown", "commit_unknown", session_id=self.session_id, reference=ref) from None
            mapped = _storage.map_error(error, outcome="not_stored", sid=self.session_id)
            if isinstance(error, sqlite3.DatabaseError):
                mapped = ResultError("write_failed", "not_stored", session_id=self.session_id, reference=ref)
            raise mapped from None

    def close(self):
        if not self._closed:
            self._store.close()
            self._closed = True

    def snapshot(self, *, budget_bytes=32_000, include_content=False):
        from ._snapshot import render
        self._check()
        try:
            return render(self.session_id, self.limits, self._store.rows(), budget_bytes, include_content)
        except (ResultError, OSError, sqlite3.DatabaseError) as error:
            raise _storage.map_error(error, sid=self.session_id) from None

    def __enter__(self):
        self._check()
        return self

    def __exit__(self, *_):
        self.close()


def read(root, reference):
    try:
        selected = Reference.parse(reference)
        return _storage.read_rows(root, selected)
    except ResultError as error:
        raise ResultError(error.code, "unavailable", session_id=error.session_id,
                          reference=error.reference) from None


def inspect_session(root, session_id):
    try:
        store = _storage.Store(root, session_id, shared=True)
    except ResultError as error:
        if error.code in ("missing", "recovery_required", "corrupt", "unsafe_storage"):
            status = "incomplete" if error.code in ("missing", "recovery_required") else "corrupt"
            return Inspection(status, (), (error.code,), getattr(error, "_observed_limits", None), False)
        raise
    try:
        rows = store.rows()
        return Inspection("complete", tuple(Reference(session_id, r[0], r[1]) for r in rows), (), store.limits, True)
    finally:
        store.close()
