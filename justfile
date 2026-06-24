[private]
default:
  @just --summary --unsorted

format:
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
