#!/bin/bash
set -euo pipefail

summary=""

# Dependencies
uv run --script .scripts/bump_deps.py
if ! git diff --quiet -- uv.lock; then
  just format
  just lint
  just test
  git commit -am "bump deps"
  summary+="✅ Bumped deps
"
elif ! git diff --quiet -- .github/workflows; then
  git commit -am "bump deps"
  summary+="✅ Bumped deps (workflows only)
"
fi

# Charms
.scripts/refresh_charms.sh
if ! git diff --quiet -- tests/functional/charms; then
  just functional
  git commit -am "refresh charms"
  summary+="✅ Refreshed charms
"
fi

# The end!
if [[ -n $summary ]]; then
  echo
  printf '%s' "$summary"
fi
