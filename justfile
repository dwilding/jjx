set ignore-comments

set shell := ["bash", "-cu"]

# Pin the container runtime for local test runs (docker or podman). The
# functional conftest skips runtime-marked tests whose marker does not match
# this value, and unmarked tests run either way. CI overrides it per matrix
# leg so marked tests get a lane of their own.
JJX_RUNTIME := env_var_or_default("JJX_RUNTIME", "docker")

set export

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

golden: (test "tests/functional/test_golden_charms.py")

[private]
test args="tests/unit tests/functional":
  uv run pytest -vv {{args}}

[private]
clean-docker:
  docker ps --all --quiet | xargs --no-run-if-empty docker rm --force
