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

golden: (test "tests/functional/test_golden_charms.py")

[private]
test args="tests/unit tests/functional":
  uv run pytest -vv {{args}}

[private]
charms:
  .scripts/refresh_charms.sh

[private]
pre-release:
  .scripts/pre_release.sh

[private]
clean-docker:
  docker ps --all --quiet | xargs --no-run-if-empty docker rm --force
