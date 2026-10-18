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
