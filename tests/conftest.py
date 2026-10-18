from pathlib import Path
import sys
import pytest
import orion_results


@pytest.fixture(autouse=True)
def installed_module():
    location = Path(orion_results.__file__).resolve()
    assert location.is_relative_to(Path(sys.prefix).resolve())
    assert "site-packages" in location.parts
