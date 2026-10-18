# orion-results

Private review candidate 0.1.0. Working name; not published. MIT is proposed, with package-specific rights and the license grant still awaiting owner confirmation.

Capture complete text in an explicitly selected private local session, pass a byte-bounded snapshot, and recover the exact text by reference. There are no pip runtime dependencies, services or automatic harness hooks.

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from orion_results import Session, Reference, inspect_session, read

with TemporaryDirectory(prefix='selected-result-example-') as temporary:
    root = Path(temporary) / 'records'
    with Session.create(root) as session:
        text = 'A synthetic result'
        expected = Reference.from_text(session.session_id, text)
        receipt = session.capture('example', text)
        assert receipt.reference == expected
        view = session.snapshot(budget_bytes=256)
        assert view.utf8_bytes == len(view.text.encode('utf-8')) <= 256
        assert read(root, expected) == text
        session_id = session.session_id
    assert inspect_session(root, session_id).status == 'complete'
```

For a reviewed local wheel, use an isolated CPython 3.11 venv and `python -m pip install --no-index --no-deps <selected-wheel>`. Run `python examples/capture_results.py` from the extracted source, with that venv's interpreter. It demonstrates large capture, safe and opt-in views, quota refusal, backup, exact recovery and owned synthetic cleanup.

The measured limit applies to the complete UTF-8 `Snapshot.text` string. Replace your selected view each time. It does not bound input memory, previous strings, wrappers, tokens or a whole model context window. Default views contain references/counts without text, labels or paths. Explicit inline selection permits complete small text as JSON, never a truncated preview. Captured text remains untrusted data.

Each session has one nonblocking cooperative writer lock. Duplicate bytes preserve the first identity/order; a reused key with changed text is distinct. Lower immutable limits are allowed. Session roots must be explicit absolute private paths. No environment/default-home discovery, network storage, multiwriter or cross-host fencing is included. References carry identity, not authorization, redaction or encryption.

Success requires transaction commit and separate read-back. A commit attempt or unconfirmed rollback/read-back leaves an unknown outcome and faults the handle. Preserve your original input and expected reference, close, inspect only that session, and explicitly recover. Missing, unsafe, corrupt or incomplete state never becomes successful emptiness. See the [operator runbook](docs/runbooks/result-capture.md) and [living contract](specs/orion-results-core-spec.md).

The demonstrated environment is CPython 3.11.14, Linux x86_64 under WSL2, local ext4, SQLite 3.45.1. Native bare-metal Linux, other Python/SQLite combinations, network filesystems, Windows/macOS, hardware power loss, JavaScript and native agent integrations remain unverified or outside v0.1. The pure-Python wheel tag does not establish those platforms.

Package uninstall preserves selected data. No garbage collector, deletion API, migration or automatic resend exists. Backup/read-back and rollback instructions are in the runbook. Tests use synthetic data; no private operating configuration or helper history is included. Build/test tools are development-only and hash locked. Proposed CI is local draft configuration, with no hosted repository or remote run yet.
