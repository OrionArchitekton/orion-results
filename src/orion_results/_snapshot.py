"""Complete snapshot rendering. The returned text is the measured boundary."""
import json
from .models import Reference, Snapshot, integer, ResultError


def render(sid, limits, rows, budget_bytes, include_content):
    integer(budget_bytes, 256, 16_777_216)
    if type(include_content) is not bool:
        raise ResultError("invalid_input", "not_stored")
    lines = []
    for digest, size, key, ordinal, payload in reversed(rows):
        line = str(Reference(sid, digest, size))
        if include_content and size <= limits.inline_bytes:
            line += " content=" + json.dumps(payload.decode("utf-8"), ensure_ascii=False)
        lines.append(line + "\n")
    text = "".join(lines)
    return Snapshot(text, len(text.encode("utf-8")), len(rows), len(rows), 0)
