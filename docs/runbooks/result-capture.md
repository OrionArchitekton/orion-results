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
