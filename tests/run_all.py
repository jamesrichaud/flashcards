"""Run every test file (tests/test_*.py), each in its own process.

    python3 tests/run_all.py

Engine and app tests go first, as CLAUDE.md asks. Exits non-zero if any file fails.
"""
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
FIRST = ["test_engine.py", "test_app.py"]


def main():
    files = FIRST + sorted(p.name for p in HERE.glob("test_*.py") if p.name not in FIRST)
    failed = []
    for name in files:
        start = time.time()
        r = subprocess.run([sys.executable, str(HERE / name)], cwd=HERE.parent,
                           capture_output=True, text=True)
        took = time.time() - start
        status = "ok" if r.returncode == 0 else "FAILED"
        print(f"{status:6} {name} ({took:.1f}s)", flush=True)
        if r.returncode != 0:
            failed.append(name)
            print((r.stdout + r.stderr)[-3000:])
    print(f"\n{len(files) - len(failed)}/{len(files)} test files passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
