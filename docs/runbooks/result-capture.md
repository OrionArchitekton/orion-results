---
verified: 2026-10-18
review_after: 2026-11-18
topics: [result-capture, local-recovery]
references: [specs/orion-results-core-spec.md, src/orion_results/session.py, src/orion_results/_storage.py]
---

# Selected result capture

Private candidate. Supply an absolute path on trusted local storage. Creation can create one root leaf and one session namespace. Both directories require effective-user ownership and mode 0700. Files require mode 0600. Existing unsafe state is refused without repair.

Use the installed Python API and preserve the session ID and full reference. Capture success requires a transaction commit and a separate read-only verification. Import and installation create no result state. Source helpers and caller configuration are unchanged.

On initialization failure, preserve the reported session ID and residue. Inspect that exact session; do not adopt it as empty or select a latest session. A fresh create selects a different namespace. Unsupported/corrupt files require deliberate investigation, never automatic deletion.

No publication, integration or automatic retention cleanup is included. Further recovery and backup procedures are added as the corresponding installed behaviors are verified.

## Selected output and quotas

Replace your current selected view with `session.snapshot().text`; do not append every historic view. The UTF-8 byte budget covers that complete string, including result references and omission counts. It does not bound caller input, wrappers, prior strings, model context, tokens or process memory. Sessions retain no all-payload cache; validation uses transient payload allocations.

Default output contains no original text, caller labels or storage paths. `include_content=True` permits complete small text as escaped JSON only when the inline and whole-view byte limits allow it. User content remains untrusted. A digest and byte length are content metadata, not redaction or encryption.

Limits can be lowered and persist for that session. Duplicate complete bytes preserve the first identity/order and use no new payload/count quota. Changed text under the same label is distinct. A quota refusal preserves earlier records. Omitted records remain enumerable with read-only inspection after closing the writer, and recoverable by exact `read(root, reference)`. Captured data has no automatic expiry.

## Ownership and unsafe state

One writer holds the existing lock inode until explicit close or process exit. A competing writer or read-only inspection reports `session_busy`; exact reads use their own consistent read-only transaction. A handle is serial-use and process-bound. Fork only while idle; the child closes its inherited descriptors without unlocking the parent's file description and cannot capture with the inherited handle. No stale-PID deletion, timeout takeover or lock replacement is permitted.

The library refuses unexpected entries, symlinks, hard links, foreign ownership, unsafe modes, substituted files, invalid markers/headers/schema/counters and inconsistent rows. It validates every advertised result under the held writer lock. Ancestors are caller-trusted; this is not protection from a hostile process running as the same user. Read-only validation never changes to writable recovery. Future formats and WAL headers refuse before a writable connection can recover or alter settings.

## Interrupted capture and exact recovery

Before submitting, retain `expected = Reference.from_text(session.session_id, text)` and your original text. Calculation alone proves no storage. Inspect only that selected session after closing its writer.

| Observed failure stage | Outcome |
|---|---|
| Invocation/guard refusal, or precommit failure with confirmed rollback | `not_stored` for this attempt |
| COMMIT attempted, failed rollback, failed postcommit read-back | `commit_unknown`; handle faulted |
| No return after process interruption | No receipt; retain uncertainty and recover by exact reference |

`not_stored` does not assert that an older matching result is absent. A faulted handle accepts no more capture/snapshot operations. Close it, then call `inspect_session(root, session_id)`. `complete` means every record and committed counter validates as of that read. `incomplete` or `corrupt` never means healthy emptiness. Busy writers and OS access errors remain explicit errors. Do not resend automatically.

Read the exact expected reference with `read(root, expected)`. Present readability is evidence of current content, not proof an earlier receipt returned. If the read-only transaction requires journal recovery, inspection reports incomplete/recovery_required and leaves files unchanged. `Session.open(root, session_id)` is the explicit writable recovery action. SQLite may replay/roll back its native journal only after the supported marker/header and private-file checks. Full schema/counter/content validation precedes further captures. Never delete a journal or recapture input to conceal incomplete state.

The native tests kill only owned synthetic writers at named precommit and postcommit barriers, including a cache-spill hot journal, and exercise SQLITE_FULL with a database page ceiling. SQL-stage I/O/rollback and read-back failures are scoped injections around real SQLite operations. Foreign UID and digest-collision observations are simulated. None of these certify actual power loss, disk controllers, network filesystems or every SQLite VFS fail point.

## Backup and restore

1. Open the selected existing session with `Session.open(root, session_id)`. Hold this writer handle throughout the copy. Do not fork/share it or start another capture. A successful `snapshot` verifies the current supported state and completes its read transaction.
2. Create a fresh private destination root/session (0700) and copy only `session.json`, `session.lock` and `results.sqlite3` into new exclusive 0600 files. Do not hard-link the lock or database. See the installed walkthrough's `copy_quiescent` routine. A live transaction filesystem copy is not a backup. Do not remove a journal to manufacture quiescence.
3. Flush/fsync copied files and their directory. Read every expected exact reference under the explicit destination root; then reopen/inspect that destination. Retain the source writer until copying and verification finish. A restored selected root is independent storage, not a new authority grant.
4. After verifying both copies, close handles normally. Keep backup locations and references under the same privacy controls as the original payloads.

No external anti-rollback history exists. An internally consistent old backup can validate; an expected newer reference remains missing, without fallback.

## Install, uninstall, upgrade and rollback

Use an isolated venv and a reviewed wheel. Runtime dependencies are standard-library-only; build/test dependencies are development-only hashed locks. Uninstall the distribution with that venv's `python -m pip uninstall orion-results`. This removes package files and leaves caller-selected capture sessions untouched. Reinstall the exact reviewed wheel and recover the same explicit references. Unsupported formats refuse; no automatic upgrade, legacy-note migration or schema rewrite occurs.

Rollback this private candidate by retaining reviewed data/backups and removing only its selected disposable venvs/build outputs. Do not switch existing helper symlinks, configs or hooks. The standalone walkthrough creates a fresh synthetic parent, verifies only its expected selected namespaces remain and removes only that parent; unexpected state is retained for inspection. General production-data removal is a separate caller decision. There is no collector/deletion API.

## Validation and monitoring

Run the installed walkthrough and installed acceptance suite. Record exact source/wheel/sdist identities, interpreter/SQLite/filesystem and the scopes of native versus injected checks. Close the writer before read-only inspection. There is no daemon or ongoing monitoring service; the caller checks receipts/errors at each selected capture/read. Preserve original text and an expected reference whenever a receipt is absent or unknown. Never count process health or a clean SQLite quick_check as proof that the requested result is recovered.
