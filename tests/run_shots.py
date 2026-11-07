"""Run every screenshot script (tests/shots_*.py), each in its own process.

    python3 tests/run_shots.py

Prints one line per script. A script that fails only because Chromium can't start
(missing system libraries) is reported as "no-chromium", not as a script bug.
Exits non-zero if any script fails for another reason.
"""
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
NO_BROWSER = ("error while loading shared libraries", "Executable doesn't exist",
              "Host system is missing dependencies")


def main():
    files = sorted(HERE.glob("shots_*.py"))
    bad, skipped = [], []
    for path in files:
        start = time.time()
        r = subprocess.run([sys.executable, str(path)], cwd=HERE.parent,
                           capture_output=True, text=True, timeout=900)
        out = r.stdout + r.stderr
        if r.returncode == 0:
            status = "ok"
        elif any(s in out for s in NO_BROWSER):
            status = "no-chromium"
            skipped.append(path.name)
        else:
            status = "FAILED"
            bad.append(path.name)
        print(f"{status:11} {path.name} ({time.time() - start:.1f}s)", flush=True)
        if status == "FAILED":
            print(out[-3000:])
    ran = len(files) - len(skipped)
    print(f"\n{ran - len(bad)}/{ran} scripts passed, {len(skipped)} could not start Chromium")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
