"""Validated immutable public values. References are identities, never authority."""
from dataclasses import dataclass, fields
import hashlib
import re

MAX_INPUT = 16_777_216
MAX_RESULTS = 1_024
MAX_PAYLOAD = 67_108_864
MAX_INLINE = 4_000


class ResultError(Exception):
    """A payload-free failure with an explicit attempt outcome."""

    def __init__(self, code, outcome="unavailable", *, session_id=None, reference=None):
        self.code = code
        self.outcome = outcome
        self.session_id = session_id
        self.reference = reference
        super().__init__(code)


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ResultError("invalid_input", "not_stored")
    return value


def utf8(value):
    if not isinstance(value, str):
        raise ResultError("invalid_input", "not_stored")
    try:
        return value.encode("utf-8", errors="strict")
    except UnicodeError:
        raise ResultError("invalid_input", "not_stored") from None


def session_identity(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{32}", value) is None:
        raise ResultError("invalid_input", "not_stored")
    return value


@dataclass(frozen=True)
class Limits:
    max_input_bytes: int = MAX_INPUT
    max_results: int = MAX_RESULTS
    max_payload_bytes: int = MAX_PAYLOAD
    inline_bytes: int = MAX_INLINE

    def __post_init__(self):
        for f, maximum in zip(fields(self), (MAX_INPUT, MAX_RESULTS, MAX_PAYLOAD, MAX_INLINE)):
            integer(getattr(self, f.name), 1, maximum)


@dataclass(frozen=True)
class Reference:
    session_id: str
    sha256: str
    utf8_bytes: int
    version: int = 1

    def __post_init__(self):
        session_identity(self.session_id)
        if type(self.version) is not int or self.version != 1:
            raise ResultError("unsupported_format", "not_stored")
        if not isinstance(self.sha256, str) or re.fullmatch(r"[0-9a-f]{64}", self.sha256) is None:
            raise ResultError("invalid_input", "not_stored")
        integer(self.utf8_bytes, 0, MAX_INPUT)

    @classmethod
    def from_text(cls, session_id, text):
        raw = utf8(text)
        if len(raw) > MAX_INPUT:
            raise ResultError("limit_exceeded", "not_stored")
        return cls(session_identity(session_id), hashlib.sha256(raw).hexdigest(), len(raw))

    @classmethod
    def parse(cls, value):
        if isinstance(value, cls):
            return value
        if not isinstance(value, str) or len(value) > 130:
            raise ResultError("invalid_input", "unavailable")
        parts = value.split(":")
        if len(parts) != 4:
            raise ResultError("invalid_input", "unavailable")
        if parts[0] != "r1":
            raise ResultError("unsupported_format", "unavailable")
        if re.fullmatch(r"0|[1-9][0-9]{0,7}", parts[3]) is None:
            raise ResultError("invalid_input", "unavailable")
        return cls(parts[1], parts[2], int(parts[3]))

    def __str__(self):
        return f"r1:{self.session_id}:{self.sha256}:{self.utf8_bytes}"


@dataclass(frozen=True)
class CaptureReceipt:
    reference: Reference
    utf8_bytes: int
    sha256: str
    deduplicated: bool


@dataclass(frozen=True)
class Snapshot:
    text: str
    utf8_bytes: int
    total_unique: int
    shown_unique: int
    omitted_unique: int


@dataclass(frozen=True)
class Inspection:
    status: str
    references: tuple[Reference, ...]
    findings: tuple[str, ...]
    limits: Limits | None
    exhaustive: bool
