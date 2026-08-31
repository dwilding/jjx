#!/bin/bash
set -euo pipefail

summary=""

# Dependencies
just deps
if ! git diff --quiet -- uv.lock; then
  just format
  just lint
  just test
  git commit -am "bump deps"
  summary+="✅ bumped deps
"
elif ! git diff --quiet -- .github/workflows; then
  git commit -am "bump deps"
  summary+="✅ bumped deps (workflows only)
"
fi

# Pebble
tag=$(curl -fsSL https://api.github.com/repos/canonical/pebble/releases/latest | jq -r .tag_name)
sed -i "s/^PEBBLE_VERSION = .*/PEBBLE_VERSION = \"$tag\"/" src/jjx/_version.py
if ! git diff --quiet -- src/jjx/_version.py; then
  just functional
  git commit -am "bump Pebble"
  summary+="✅ bumped Pebble to $tag
"
fi

# Juju
tag=$(curl -fsSL https://api.github.com/repos/juju/juju/releases/latest | jq -r .tag_name)
version=${tag#v}
[[ $version == 4.* ]]
sed -i "s/^JUJU_VERSION = .*/JUJU_VERSION = \"$version\"/" src/jjx/_version.py
if ! git diff --quiet -- src/jjx/_version.py; then
  just functional
  git commit -am "bump Juju"
  summary+="✅ bumped Juju to $version
"
fi

# Charms
just charms
if ! git diff --quiet -- tests/functional/charms; then
  just functional
  git commit -am "refresh charms"
  summary+="✅ refreshed charms
"
fi

# The end!
if [[ -n $summary ]]; then
  echo
  printf '%s' "$summary"
fi
