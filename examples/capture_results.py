"""Synthetic installed-API walkthrough; never selects an existing data directory."""
from pathlib import Path
from tempfile import TemporaryDirectory
from orion_results import Session, Reference, read


def main():
    with TemporaryDirectory(prefix='result-capture-demo-') as selected:
        root=Path(selected)/'records'
        large='synthetic large result|'*5000
        with Session.create(root) as session:
            expected=Reference.from_text(session.session_id,large)
            receipt=session.capture('large',large)
            session.capture('small','Hello from a synthetic result.')
            assert receipt.reference==expected
            assert read(root,expected)==large
            view=session.snapshot()
            assert view.utf8_bytes==len(view.text.encode('utf-8'))
            print(f'input_bytes={expected.utf8_bytes} snapshot_bytes={view.utf8_bytes}')
            assert 'synthetic large result' not in view.text
            assert 'Hello' in session.snapshot(include_content=True).text


if __name__=='__main__':
    main()
