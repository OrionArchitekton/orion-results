"""Build a fresh installed candidate for each recorded acceptance cycle."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import time

p = argparse.ArgumentParser()
p.add_argument("--phase", required=True)
p.add_argument("nodes", nargs="*")
a = p.parse_args()
root = Path(__file__).resolve().parent.parent
out = Path(tempfile.mkdtemp(prefix=a.phase + "-", dir=root / ".private"))
build_python = root / ".venv-build/bin/python"
test_python = root / ".venv-test/bin/python"
commands = [
    [str(build_python), "-m", "build", "--no-isolation", "--outdir", str(out), str(root)],
    [str(test_python), "-m", "pip", "install", "--no-index", "--no-deps", "--force-reinstall",
     str(out / "orion_results-0.1.0-py3-none-any.whl")],
    [str(test_python), "-I", "-m", "pytest", "--import-mode=importlib", "-p", "no:cacheprovider",
     *(a.nodes or [str(root / "tests")]), "-q"],
]
record = {"phase": a.phase, "time_ns": time.time_ns(), "steps": []}
code = 0
for n, cmd in enumerate(commands):
    with (out / f"{n}.log").open("w") as log:
        run = subprocess.run(cmd, cwd=root, stdout=log, stderr=subprocess.STDOUT)
    record["steps"].append({"command": cmd, "exit_code": run.returncode})
    if run.returncode:
        print((out / f"{n}.log").read_text()[-6500:])
        code = run.returncode
        break
for f in out.glob("*.whl"):
    record["wheel_sha256"] = hashlib.sha256(f.read_bytes()).hexdigest()
(out / "cycle.json").write_text(json.dumps(record, indent=2) + "\n")
print(json.dumps({"phase": a.phase, "exit_code": code, "evidence": str(out)}))
raise SystemExit(code)
