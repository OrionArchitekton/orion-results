# Bounded result capture: Python core specification

Status: owner-approved private implementation contract, 2026-10-18. The owner approved implementation after reviewing the spec and extraction plan. The owner approved the reviewed package, MIT and proposed names; publication and private-runtime adoption remain separate actions. `orion-results` / `orion_results` are the owner-approved distribution/import names.

## Outcome and scope

A Python caller captures text into a private local capture session, obtains a verifiable result reference, and requests one bounded snapshot to pass to another consumer. The consumer can explicitly recover the exact stored text. Missing, unsafe, corrupt or indeterminate storage never becomes successful recovery or an empty healthy snapshot.

The v0.1 candidate is a standard-library Python library using its embedded `sqlite3` module, with no pip runtime dependency or database service. Initial support targets CPython 3.11 on Linux x86_64 with local ext4, including a separately demonstrated WSL2/ext4 environment. Record the linked SQLite version and required transaction/PRAGMA capabilities in the native receipt. Release support requires the exact-artifact receipt; this spec itself establishes no tested platform. Known unsupported platform/dependency conditions are reported before creating state; failures discovered during filesystem initialization retain an explicit incomplete namespace. Network filesystems, Windows/macOS, hostile same-user processes, streaming ingestion, automatic harness hooks, JavaScript parity, daemons and remote storage are outside v0.1.

## Terminology and measured promise

| Canonical term | Meaning |
|---|---|
| Capture session | An independently owned local namespace of unique captured text and its immutable limits. This replaces the old ambiguous use of context/accumulator as the unit of storage. |
| Result record | A committed private record containing complete text, its full SHA-256 identity and bounded metadata. This replaces note as the canonical storage term. |
| Result reference | Versioned session identity, full SHA-256 and original UTF-8 byte count. It selects content; it grants no authority. Pointer is a presentation synonym only. |
| Snapshot | The exact UTF-8 text returned in `Snapshot.text`. This is the sole output to which the aggregate byte budget applies. |
| Stored | A capture completed the specified transaction commit and read-back steps and returned a success receipt. |
| Unknown commit | Capture may have committed a record but did not return verified success. Inspection and reconciliation are required. |

For every successful snapshot, `snapshot.utf8_bytes == len(snapshot.text.encode("utf-8")) <= budget_bytes`. Count all headers, separators, inline text, result references and omission information in that text. Object attributes, callers' wrappers, previously returned strings, original input, process memory and a model/vendor context window are outside this bound. Documentation must tell callers to replace their selected view with the new snapshot, rather than append every historical snapshot or `capture` receipt.

Capture receipts and inspection metadata are not automatically bounded prompt text. They contain no original payload; callers must select how they log them. The library never claims to redact secrets, encrypt local state or enforce a whole-agent token limit.

## Selected approach

| Approach | Tradeoff | Decision |
|---|---|---|
| One-shot trimming only | Smallest interface, but cannot satisfy aggregate output, verified retrieval or session recovery. | Insufficient for this selected outcome. |
| Private SQLite session plus a bounded snapshot | Immutable logical records and committed counters change in one embedded transaction. Avoids a custom record/index commit protocol and has no shared overflow file. | Selected for v0.1. |
| Streaming/process-memory control, multiwriter API or storage-provider framework | Broader input/memory/concurrency problem with additional lifecycle and compatibility commitments. | Deferred. |

One public module hides the storage and rendering implementation. One selected embedded local store is sufficient. A directory of independent record files was considered and rejected: without a committed index it cannot detect a missing record/healthy subset, while adding that index creates a multi-file recovery protocol. SQLite transactions keep content and membership/counters together. This is a new candidate-owned schema, not a mutation or migration of any estate store. Test callers and product callers cross the same installed Python seam.

## Public interface

```python
Session.create(root, *, limits=Limits()) -> Session
Session.open(root, session_id) -> Session
session.capture(key: str, text: str) -> CaptureReceipt
session.snapshot(*, budget_bytes=32_000, include_content=False) -> Snapshot
session.close() -> None
Reference.from_text(session_id, text: str) -> Reference
read(root, reference: Reference | str) -> str
inspect_session(root, session_id) -> Inspection
```

`Session` supports a context manager and read-only `session_id` and `limits` attributes. Root is an explicitly supplied absolute filesystem path; it is not a SQLite URI and receives no implicit home/environment expansion. No estate path, latest-session selection, search of other roots or vendor-session discovery exists. Import and package installation perform no state creation. Session handles are process-bound, serial-use objects; threads and forked children must not share them. Read-only recovery uses a separate installed call and does not need the writer handle.

`CaptureReceipt` is immutable: `reference`, `utf8_bytes`, `sha256`, `deduplicated`. It is returned only for verified stored/duplicate content. `Snapshot` is immutable: `text`, `utf8_bytes`, `total_unique`, `shown_unique`, `omitted_unique`. Counts describe unique content at that observation, not tool executions. `Inspection` reports `complete`, `incomplete` or `corrupt`, validated references, bounded finding codes and the selected session's limits when valid metadata is available. A partial inspection never invents missing limits or claims completeness.

`Reference` has version 1, a lowercase 32-hex session ID, a lowercase 64-hex full SHA-256 and a nonnegative UTF-8 byte count within the global input ceiling. Capture/read additionally enforce a selected session's lower configured limit. Its text form is `r1:<session_id>:<sha256>:<utf8_bytes>`; only canonical ASCII forms are accepted. `Reference.from_text` is pure identity calculation, not evidence of submission, storage or recoverability. The caller can retain it before capture to inspect an interrupted operation exactly. References have no shell/path/URL semantics and cause no fetch or execution.

`read` checks the reference against the selected session and the actual record, including complete payload length and digest. It returns byte-identical text or raises an explicit error; it never selects an older/different record. A copied session may be read under an explicitly selected new private root after all checks. A reference is neither authentication nor proof of continued availability.

## Inputs, deduplication and limits

- Accept Python `str` text with strict UTF-8 encoding, including empty text. Reject lone surrogates and wrong types before mutation. Preserve text exactly; no normalization or automatic redaction.
- A key is nonempty text of at most 256 UTF-8 bytes, stored as a bound database value. It never supplies SQL, a filename or a command. When deliberately serialized for diagnostics, control characters remain escaped. Caller labels grant no privilege.
- `Limits` is immutable, with defaults `max_input_bytes=16_777_216`, `max_results=1_024`, `max_payload_bytes=67_108_864` and `inline_bytes=4_000`. Limits are positive integers, reject booleans and become immutable session metadata. v0.1 permits lowering these defaults, not raising them. The immutable session marker is at most 4,096 bytes; the database file has a separate 134,217,728-byte safety ceiling. Payload quotas count actual UTF-8 BLOB bytes, not SQLite pages, metadata, journal files or total filesystem allocation. No whole-disk usage bound is claimed.
- Duplicate identity is complete text bytes within one selected session, verified by full digest, size and byte comparison with the existing valid record. Duplicates return the same reference and `deduplicated=True`, consume no new record/quota and keep the first key/order. Different keys are not a durable audit trail of repeated calls. Different text under a reused key creates a distinct result.
- A digest collision or mismatching existing content is `content_conflict`, never a dedup hit or an overwrite. Dedup does not span sessions. Quota checks and content/counter changes occur in the same transaction under the writer lock. Quota failure leaves earlier records and the snapshot intact. The reader verifies committed counters against the actual rows; deleting a row without its matching transactional state is corruption, not a smaller healthy session.
- The session retains bounded metadata rather than keeping every original payload on its long-lived object. Input/serialization transient allocations are not a process-memory guarantee.

## Snapshot behavior

The budget is an integer from 256 through 16,777,216 bytes; booleans and values outside that range are invalid. An empty valid session yields empty text and zero counts. Failed inspection/storage cannot yield that empty success.

Default snapshots contain result references and bounded session/count information, with **no keys, paths, payload text or head preview**. Full digests and lengths are content metadata; they are not a secret-redaction guarantee. `include_content=True` explicitly permits complete small texts, encoded as JSON strings to preserve text/control characters. There is no truncated preview mode in v0.1. A text that exceeds the inline limit or remaining budget uses its reference instead.

Present most recent unique results first; repeat captures do not reorder old content. Reserve room for one session reference/count line before admitting individual lines. If not every result fits, one bounded omission line identifies the exact session and omitted/total counts; callers use `inspect_session` to enumerate references explicitly. It never suggests that omitted records were deleted or already supplied to the downstream consumer. All line formatting is deterministic and counted after UTF-8 encoding. Never cut a code point or emit an unresolvable fake result reference to make the bound fit.

A snapshot verifies the selected session's current records under its already-held writer lock before advertising them as available; it does not recursively acquire the public inspection lock. Corrupt/missing/unreadable state raises an error; it does not reuse a cached healthy view. Snapshot validity is as-of that read, not a guarantee that files cannot later change. Every later `read` revalidates the exact reference. Replacing the entire database with an internally consistent older backup is not detected by an external monotonic history; the package supplies no anti-rollback ledger. An expected reference missing from that selected copy remains missing, without fallback. User content remains untrusted data even when JSON-escaped.

## Private storage and concurrency

Each `create` makes a new unpredictable session subdirectory under an explicit private root. The root may be newly created as one leaf beneath an existing caller-selected parent, or an existing private directory. Existing unsafe roots are refused without chmod or repair. No recursive creation or scanning of unrelated roots is allowed.

The selected root/session directories must be owned by the effective user, have mode 0700 and be opened as directories without following a final symlink. Session marker, database, lock and SQLite journal files use mode 0600 and verified owner/type. Use descriptor-relative validation/creation below validated directory descriptors. SQLite's pathname open is inside that private directory; verify its precreated file identity before/after open. Ancestors are caller-trusted; this is not isolation from an attacker able to replace trusted paths or run as the same user. The implementation must not change process-global umask to obtain privacy.

One cooperative writer per session holds a nonblocking kernel file lock on a stable lock inode; release occurs on handle close/process exit. No TTL, stale-PID deletion, lock-file replacement or force takeover. Independent sessions share no record, overflow, journal or writer-lock files. A second writer receives `session_busy`; permission failures remain unavailable, rather than busy. A writer lock is a local cooperative mechanism, not cross-host fencing. Handles are not inherited across exec and cannot be used after fork; the implementation must close inherited child descriptors without unlocking the parent's lock.

An immutable bounded session marker fixes the format version, session ID and limits. Its JSON decoder rejects unknown/duplicate fields, NaN/Infinity and wrong types. An embedded database holds one matching metadata row and content rows: full digest, original byte count, first key, unique insertion ordinal and original strict UTF-8 bytes as a BLOB. Its metadata contains committed count, aggregate payload bytes and next ordinal, updated in the same transaction as a new row. No record update/delete API exists. Validate exact expected schema/role/version and parameterize all values; no user SQL, extension loading, triggers or views are admitted.

Readers validate fixed file names, owner/mode/type/link count and file sizes before opening; reject symlinks, hard-linked database/marker/lock/journal files and unrecognized entries. Database safety size is enforced before reads and through a checked page-count limit before writes. Before any writable connection can invoke recovery, validate the immutable marker and raw SQLite header's magic, application ID, user-version and rollback-journal mode. Unsupported roles/versions/WAL modes refuse without changing the database. The actual schema, counters, row types, ordinals, UTF-8 sizes and digests must then agree. Ordinals are exactly the unique committed sequence and the next ordinal equals its count. An absent database is missing/incomplete, never automatically created by a read or reopen.

Create the database exclusively with private permissions in the fresh namespace. Reopen existing databases with explicit SQLite `mode=rw`; read/inspect use `mode=ro` and query-only connections. Select and read back `journal_mode=DELETE` and `synchronous=EXTRA` for writable sessions, plus the page-count ceiling; use memory-only SQLite temporary storage. No WAL mode, pooled connections or multiwriter API. Capture uses an explicit `BEGIN IMMEDIATE`, verifies quotas/dedup, writes the immutable row and its counters together, commits, then verifies the committed reference and counters through a separate read-only connection before returning a receipt. Reads/snapshots/inspection each observe one consistent read transaction, not a mixture of statements from different commits. A failed commit or failed post-commit verification is indeterminate even if an earlier call appeared successful.

`create` syncs/verifies its bounded marker, database initialization, session directory and parent before returning a usable handle. Incomplete initialization stays visible and is never adopted as empty. This is observed native commit/read-back on the supported filesystem; source tests do not establish controller or real power-loss behavior. SQLite owns journal replay/rollback: it is not a hand-maintained application journal or an event log imported from another product.

## Errors, interruption and recovery

One public `ResultError` carries a stable `code`, `outcome`, optional selected `session_id` and optional exact `reference`. Report the chosen session ID when initialization has created a namespace but cannot return a usable handle, so that residue is inspectable without searching for a latest session. Payload text and caller keys are excluded from error messages. Codes are machine-readable; callers do not parse prose or receive fabricated success objects. `not_stored` describes this failed capture attempt; it does not assert that matching content from an earlier successful capture is absent.

| Code family | Meaning / mutation classification |
|---|---|
| `invalid_input`, `limit_exceeded`, `unsupported_platform` | Invocation refused before publishing a record; capture outcome `not_stored`. |
| `unsafe_storage`, `unsupported_format`, `session_busy`, `closed_session`, `wrong_process` | Selected operation refused. Capture outcome `not_stored`; no implicit repair or alternate session. |
| `storage_unavailable`, `write_failed` | A failure before commit is `not_stored` only when rollback/non-submission is confirmed. A rollback failure is unknown. Preserve original caller text and selected-session evidence. |
| `commit_unknown` | Commit was attempted, rollback could not be confirmed, or post-commit verification failed. No success receipt; the writer handle becomes faulted and accepts no further capture/snapshot. |
| `missing`, `corrupt`, `content_conflict`, `recovery_required` | Exact referenced/selected state cannot satisfy the requested operation. Read/inspection outcome is `unavailable`; capture refusal is `not_stored` unless commit/rollback may be indeterminate, in which case report `commit_unknown`. |

Successful inspection is read-only and attempts a shared nonblocking lock on the existing lock inode. A live writer yields `session_busy`; a missing lock is incomplete state, not a newly created lock. `complete` requires verified marker/database identities, supported schema, consistent counters, all valid records and a successful read-only transaction without required journal recovery or unrecognized entries. A safe cold journal is not classified solely by its presence; let SQLite determine whether a read requires recovery. `incomplete` identifies initialization or native journal recovery still required; `corrupt` identifies invalid committed state, an unsafe/unrecognized namespace or count/row mismatch. A validated subset is labelled non-exhaustive while state is incomplete. Unsupported versions and OS access failures remain explicit errors. Read-only inspection never falls back to a writable connection for recovery.

After an unknown result: retain the precomputed reference and original text, stop using the faulted handle, close it and inspect only that session. Read the exact reference if possible. A valid read establishes present readability, not an earlier returned receipt. If SQLite requires journal recovery, `Session.open` is the caller's explicit writable recovery action: under the exclusive lock, validate marker/file safety and supported role/version first, allow SQLite's normal rollback-journal recovery, then revalidate all records/counters before permitting writes. It never recaptures the input. A later explicit duplicate capture can establish current success after verified read-back. Invalid metadata/unsafe files are not repaired. No automatic resend, application journal deletion, older-record fallback or conversion of a missing/unavailable database into healthy emptiness.

Retention is indefinite until the caller deliberately removes selected data. Package uninstall preserves captured sessions. v0.1 includes no collector or deletion API. For backup, hold the selected session's writer handle without an active transaction, verify a complete state, and copy only the documented quiescent backup set to a fresh private root while retaining that lock. Do not copy an active transaction or delete a journal to make a backup look complete. The runbook covers restored read-back, residue review and removal only of a separately owned disposable walkthrough directory. Broader production-data removal remains a user-selected filesystem operation. Reinstallation does not migrate old helper notes or rewrite store versions.

## Vertical scenarios and acceptance criteria

The primary seam is the public API of a **non-editable wheel installed in an isolated environment**, against a private disposable local filesystem. Subprocess consumers use that same installed wheel. A small fault seam at actual filesystem operations supplies deterministic errors and interruption barriers; it does not replace the storage implementation with an in-memory fake. Static dependency/contents checks supplement behavior. No provider, browser, phone or harness session is needed to prove this library.

| ID | Complete observable slice | Acceptance |
|---|---|---|
| S1 | A caller creates a private session, captures a small result and recovers it through a separate installed reader. | Strict inputs; private root/session/record modes under umask 022 and 077; exact reference/digest/length; no install-time state; exact text survives close/reopen; no source-tree imports. |
| S2 | A large result is captured, its safe default snapshot is passed to a consumer, and the exact content is recovered on request. | No payload/key/path preview in default text; opt-in inline content obeys size/encoding rules; full read-back including Unicode/control characters/empty text; altered, missing, wrong-session or future-version references remain explicit; earlier records are never substituted. |
| S3 | Repeated and many distinct results produce a bounded current view, with recoverable omitted content. | Dedup retains first identity/order without an extra record; changed content under the same key is distinct; every complete `Snapshot.text` fits budgets including 256 bytes; valid UTF-8, escaped inline text and correct omission counts; all committed unique references remain enumerable/readable; previously returned strings/process memory are explicitly outside the promise. Payload/count quota failures preserve earlier results; SQLite storage exhaustion is an explicit storage failure. |
| S4 | Two independent sessions and two processes contending for one session behave predictably. | Independent captures cannot overwrite each other. A competing writer is busy; actual process exit releases ownership; the next writer sees existing valid records; no force takeover or lock inode replacement. Symlink/hardlink/owner/mode/type/path-substitution and malformed bounded metadata cases refuse without touching an unrelated sentinel. Forked handles cannot write or unlock a parent's handle. |
| S5 | A write is interrupted or fails at transaction/commit stages and is then inspected through the installed API. | Before commit, no success receipt or silently truncated fallback; `not_stored` requires confirmed rollback. Once commit is attempted or rollback is indeterminate, `commit_unknown`/no-return stays unknown; handle faulted. Exact read/inspection distinguishes complete rows/counters, journal recovery, missing/corrupt state and OS unavailability. Explicit writable reopen permits native SQLite recovery and full revalidation, never recapture. Removing a row without updating its committed count cannot produce a healthy subset. |
| S6 | A user backs up/restores a quiescent selected session while retaining its writer lock, then upgrades/uninstalls the candidate independently of their data. | Restored reference identity/content verifies under the explicit new private root; unsupported formats refuse mutation; package uninstall leaves sessions intact; reinstallation reads the supported version. The synthetic walkthrough removes only its own temporary parent after successful cleanup checks. Original private helper, other sessions and existing packages remain unchanged. |
| S7 | A clean consumer follows the exact private release packet using built wheel/sdist and the standalone guide. | Hash-verified artifacts install from fresh environments, the walkthrough demonstrates store/reference/snapshot/recovery/refusal, source archives carry the living spec/runbook/notices, and artifact contents contain no estate config/history/caches/real records. Source, built artifact, evidence, guide and Works copy share the same bounded claim. No stable-newcomer, native-harness or future-composition claim is inferred. |

## Ecosystem compatibility

The library owns only selected result records and snapshot behavior. PM continues owning tasks/claims; browser/phone own their native resource lifecycles; Handoff owns selected context revisions. A caller may attach a result reference, digest and observation scope to their existing evidence/context interface after an optional adapter is separately tested. This library imports none of those products and never edits their state directly.

No task/actor string, result reference, captured instruction, snapshot, model judgment or inspection grants authority. Sender/receiver operation IDs remain caller concerns; content deduplication is not a general exactly-once execution or request-idempotency protocol. A read of a reference is not execution of its text. No current starter pin or released component API changes to accommodate this package. Private estate adoption is a later migration with its own approval, backup and consumer proof.

## Evidence, support and release review

The living code contract is `specs/orion-results-core-spec.md`. It was copied before implementation and accompanies this private candidate. Contract text is not test evidence; consult the exact-artifact verification packet for observed behavior.

Before claiming the release packet ready, retain exact source/tree identity, built wheel/sdist digests and member lists, installed S1-S7 outcomes, transaction fail-point observations, independent-session/one-writer proof, local platform/filesystem/linked-SQLite identity, dependency/secret scans, source lineage, license/notices, and original-helper preservation hashes. Sanitize public receipts separately. An injected interruption proves only the named stage; it is not a real power-loss test. Source tests for the old helper do not satisfy these scenarios.

Prepare a standalone quickstart and recovery runbook covering configuration, allowed storage effects, limits, known/unknown commits, explicit inspection, backup/read-back, upgrade, uninstall and rollback. The runbook's freshness frontmatter names its verified date, review-after date, topics and references, set from actual evidence. Prepare Works/guide copy alongside the artifact before full-package review. A public guide video is separately selected and creatively reviewed.

The owner approved the package-specific review, MIT and proposed repository destinations. Later decisions: refreshed exact artifacts/site/guide acceptance, qualified protected reviews where required, and the explicit outward release set. Build/preparation is not publication authorization. No new public/private hosted repository, tag, registry upload, deployment, permission grant or runtime change is included in this private implementation task.

## Feasibility references

These primary references support feasibility, not a finished implementation or platform certification: Python [sqlite3](https://docs.python.org/3.11/library/sqlite3.html) exposes embedded transactions and read-only URI connections; SQLite explains [atomic commit](https://www.sqlite.org/atomiccommit.html), [synchronization modes](https://www.sqlite.org/pragma.html#pragma_synchronous), [file headers](https://www.sqlite.org/fileformat.html) and [journal recovery](https://www.sqlite.org/lockingv3.html). Python [os](https://docs.python.org/3.11/library/os.html) and [fcntl](https://docs.python.org/3.11/library/fcntl.html) cover private-file and local-lock primitives. The session marker requires explicit strict [JSON decoding](https://docs.python.org/3.11/library/json.html). Linked-runtime capability checks, actual-file privacy, transaction/error and contention demonstrations remain mandatory.
