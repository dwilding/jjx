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
charms:
  #!/bin/bash
  set -euo pipefail
  rm -rf tests/functional/charms/*
  git clone --depth 1 --single-branch https://github.com/canonical/operator.git
  cp -r operator/examples/httpbin-demo tests/functional/charms
  rm -rf tests/functional/charms/httpbin-demo/spread
  cp -r operator/examples/k8s-2-configurable tests/functional/charms
  cp -r operator/examples/k8s-4-action tests/functional/charms
  pushd tests/functional/charms/k8s-4-action
  charmcraft fetch-libs
  popd
  rm -rf operator
