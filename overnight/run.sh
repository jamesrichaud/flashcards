#!/usr/bin/env bash
# Runs Claude Code repeatedly, one PLAN.md item per run, until the plan is done or time runs out.
# Everything is wrapped in main() so bash reads the whole file first: a run may edit this script.
main() {
# Usage (from the repo folder):  HOURS=9 ./overnight/run.sh
cd "$(dirname "$0")/.." || exit 1
mkdir -p logs
LOG=logs/overnight.log
echo "=== start $(date) ===" >> "$LOG"

# Python tools the tests need, in a private folder inside the repo.
if [ ! -d .venv ]; then
  python3 -m venv .venv || { sudo apt-get install -y python3-venv && python3 -m venv .venv; }
fi
. .venv/bin/activate
pip install -q anki fastapi "uvicorn[standard]" python-multipart itsdangerous httpx playwright pywebpush tzdata >> "$LOG" 2>&1
python -m playwright install chromium >> "$LOG" 2>&1 || true

END=$(( $(date +%s) + ${HOURS:-9} * 3600 ))
TOOLS="Read,Edit,Write,Glob,Grep,WebFetch,Bash(python:*),Bash(python3:*),Bash(pip:*),Bash(git add:*),Bash(git commit:*),Bash(git status:*),Bash(git diff:*),Bash(git log:*),Bash(git checkout -- :*),Bash(ls:*),Bash(cat:*),Bash(head:*),Bash(tail:*),Bash(mkdir:*),Bash(grep:*),Bash(find:*),Bash(wc:*),Bash(cp:*),Bash(mv:*)"
run=0
while [ "$(date +%s)" -lt "$END" ] && grep -q '^- \[ \]' PLAN.md; do
  run=$((run + 1))
  item=$(grep -m1 '^- \[ \]' PLAN.md | cut -c7-70)
  echo "=== run $run $(date +%H:%M) : $item ===" | tee -a "$LOG"
  claude -p "$(cat overnight/next_task.md)" \
    --permission-mode acceptEdits --allowedTools "$TOOLS" --max-turns 250 >> "$LOG" 2>&1
  code=$?
  git push -q >> "$LOG" 2>&1 || true
  if [ $code -ne 0 ]; then
    echo "Claude stopped (code $code), probably a usage limit. Waiting 20 minutes." | tee -a "$LOG"
    sleep 1200
  else
    sleep 20
  fi
done

echo "=== finished $(date) ===" | tee -a "$LOG"
grep -E '^- \[(x|~| )\]' PLAN.md | cut -c1-80 | tee -a "$LOG"
docker compose up -d --build >> "$LOG" 2>&1 && echo "App rebuilt and running on http://localhost:8000" | tee -a "$LOG"
}
main "$@"; exit
