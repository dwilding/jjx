set ignore-comments

[private]
default:
  @just --summary --unsorted

format:
  uv run ruff check --fix
  uv run ruff format

lint:
  uv run ruff check
  uv run ruff format --diff
  uv run ty check

unit: (test "tests/unit")

functional: (test "tests/functional")

[private]
test args="tests/unit tests/functional":
  uv run pytest -vv {{args}}

[private]
deps:
  uv run --script .scripts/bump_deps.py

[private]
pebble:
  #!/bin/bash
  set -euo pipefail
  latest=$(curl -fsSL https://api.github.com/repos/canonical/pebble/releases/latest | jq -r .tag_name)
  sed -i "s/^PEBBLE_VERSION = .*/PEBBLE_VERSION = \"$latest\"/" src/jjx/_version.py
  echo "Set PEBBLE_VERSION to $latest"

[private]
charms:
  #!/bin/bash
  set -euo pipefail
  rm -rf tests/functional/charms/*
  rm -rf operator
  git clone --depth 1 --single-branch https://github.com/canonical/operator.git
  cp -r operator/examples/k8s-2-configurable tests/functional/charms
  rm -rf operator
  uv run --script .scripts/patch_charms.py
  cd tests/functional/charms/k8s-2-configurable
  UV_NO_CONFIG=1 tox -e format,lint,unit

[private]
pre-release:
  @echo 'Do these steps before each release. If any step fails, stop and investigate the failure - don'\''t continue the process.'
  @echo ''
  @echo '1. `just deps`. If `uv.lock` changed:'
  @echo '  a. `just format`'
  @echo '  b. `just lint`'
  @echo '  c. `just test`'
  @echo '  d. `git commit -am "bump deps"`'
  @echo '1. (continued) If only workflow YAML files changed:'
  @echo '  a. `git commit -am "bump deps"`'
  @echo '2. `just pebble`. If `PEBBLE_VERSION` changed:'
  @echo '  a. `just functional`'
  @echo '  b. `git commit -am "bump Pebble"`'
  @echo '3. `just charms`. If any files changed:'
  @echo '  a. `just functional`'
  @echo '  b. `git commit -am "refresh charms"`'

[private]
clean-docker:
  docker ps --all --quiet | xargs --no-run-if-empty docker rm --force
