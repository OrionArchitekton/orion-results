# Private result-capture candidate

Owner-approved private implementation. No remote, publication, runtime adoption or new hooks.
Work sequentially in this isolated worktree, one installed public-API RED/GREEN cycle at a time.
Contract: specs/orion-results-core-spec.md. CPython 3.11/Linux local ext4 only until proved otherwise.
Checks: tools/check.py builds a fresh non-editable wheel, installs in .venv-test, runs isolated pytest.
Final: full installed suite, sdist rebuild/install, dependency/member audits and gitleaks report.
Private evidence under .private/ is excluded from archives. Owner approved the reviewed package, MIT and proposed names on 2026-10-18. Publication and runtime adoption remain separate gates.
