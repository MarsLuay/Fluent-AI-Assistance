#!/bin/zsh
set -u

SCRIPT_DIR="${0:A:h}"
APP_BUNDLE="${SCRIPT_DIR:h:h}"
REPO_ROOT="${APP_BUNDLE:h}"
LAUNCHER="source/tools/simulator/launch_simulator.py"

while [[ -n "$REPO_ROOT" && "$REPO_ROOT" != "/" ]]; do
  if [[ -f "$REPO_ROOT/$LAUNCHER" ]]; then
    break
  fi
  REPO_ROOT="${REPO_ROOT:h}"
done

if [[ ! -f "$REPO_ROOT/$LAUNCHER" ]]; then
  echo "Could not find $LAUNCHER."
  echo "Move this launcher into the Fluent AI-Assistance folder, then try again."
  read -k 1 "?Press any key to close..."
  exit 1
fi

cd "$REPO_ROOT" || exit 1

if command -v python3 >/dev/null 2>&1; then
  exec python3 "$LAUNCHER" "$@"
fi

if command -v python >/dev/null 2>&1; then
  exec python "$LAUNCHER" "$@"
fi

echo "Python was not found on PATH."
echo "Install Python 3, then reopen this launcher."
read -k 1 "?Press any key to close..."
exit 1
