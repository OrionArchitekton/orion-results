"""Complete snapshot rendering. The returned text is the measured boundary."""
import json
from .models import Reference, Snapshot, integer, ResultError


def render(sid, limits, rows, budget_bytes, include_content):
    integer(budget_bytes, 256, 16_777_216)
    if type(include_content) is not bool:
        raise ResultError("invalid_input", "not_stored")
    total = len(rows)
    if total == 0:
        return Snapshot("", 0, 0, 0, 0)
    def summary(shown, omitted):
        return f"session={sid} total={total} shown={shown} omitted={omitted}\n"
    reserved = len(summary(total, total).encode("utf-8"))
    lines = []
    used = 0
    for digest, size, key, ordinal, payload in reversed(rows):
        reference = str(Reference(sid, digest, size)) + "\n"
        line = reference
        if include_content and size <= limits.inline_bytes:
            complete = reference[:-1] + " content=" + json.dumps(payload.decode("utf-8"), ensure_ascii=False) + "\n"
            if used + len(complete.encode("utf-8")) + reserved <= budget_bytes:
                line = complete
        size = len(line.encode("utf-8"))
        if used + size + reserved > budget_bytes:
            break
        lines.append(line)
        used += size
    shown = len(lines)
    text = "".join(lines) + summary(shown, total - shown)
    return Snapshot(text, len(text.encode("utf-8")), total, shown, total - shown)
