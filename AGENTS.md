# Result-capture core

Own only complete local result capture, bounded snapshots and exact recovery.
Runtime adoption, services and agent hooks require separate approval.
Work sequentially in this isolated worktree, one installed public-API RED/GREEN cycle at a time.
Contract: specs/orion-results-core-spec.md. CPython 3.11/Linux local ext4 only until proved otherwise.
Checks: tools/check.py builds a fresh non-editable wheel, installs in .venv-test, runs isolated pytest.
Final: full installed suite, sdist rebuild/install, dependency/member audits and gitleaks report.
Private evidence under .private/ is excluded from archives. MIT and the public release destinations are approved. Publish only accepted exact artifacts after final-head CI and qualified human review. Runtime adoption remains separate.
