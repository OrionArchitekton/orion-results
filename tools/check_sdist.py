"""Rebuild and exercise one explicit source archive in a fresh environment."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
import venv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    archive = args.archive.resolve(strict=True)
    directory = args.directory.absolute()
    directory.mkdir(mode=0o700)  # Refuse an existing consumer environment.
    source_parent = directory / "source"
    source_parent.mkdir(mode=0o700)
    with tarfile.open(archive) as selected:
        for member in selected.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or not (member.isfile() or member.isdir()):
                raise ValueError("source archive contains an unsafe member")
            target = source_parent.joinpath(*path.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as output, selected.extractfile(member) as content:
                    output.write(content.read())
    roots = list(source_parent.iterdir())
    if len(roots) != 1 or not roots[0].is_dir():
        raise ValueError("expected one source root")
    source = roots[0]
    environment = directory / "environment"
    venv.EnvBuilder(with_pip=True).create(environment)
    python = str(environment / "bin/python")
    rebuilt = directory / "rebuilt"
    record = {"source_archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(), "steps": []}

    def run(label, command):
        with (directory / (label + ".log")).open("w") as log:
            result = subprocess.run(command, cwd=source, stdout=log, stderr=subprocess.STDOUT)
        record["steps"].append({"label": label, "exit_code": result.returncode})
        (directory / "verification.json").write_text(json.dumps(record, indent=2) + "\n")
        if result.returncode:
            print((directory / (label + ".log")).read_text()[-6500:])
            raise SystemExit(result.returncode)

    run("locked-tools", [python, "-m", "pip", "install", "--require-hashes", "-r", str(source / "dev-requirements.lock")])
    run("build", [python, "-m", "build", "--no-isolation", "--outdir", str(rebuilt), str(source)])
    wheels = list(rebuilt.glob("*.whl"))
    if len(wheels) != 1:
        raise ValueError("expected one rebuilt wheel")
    record["rebuilt_wheel_sha256"] = hashlib.sha256(wheels[0].read_bytes()).hexdigest()
    run("install", [python, "-m", "pip", "install", "--no-index", "--no-deps", str(wheels[0])])
    run("installed-suite", [python, "-I", "-m", "pytest", "--import-mode=importlib", "-p", "no:cacheprovider", str(source / "tests"), "-q"])
    run("example", [python, "-I", str(source / "examples/capture_results.py")])
    run("pip-check", [python, "-m", "pip", "check"])
    print(json.dumps({"exit_code": 0, "evidence": str(directory / "verification.json")}))


if __name__ == "__main__":
    main()
