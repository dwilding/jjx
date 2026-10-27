# AGENTS.md

## Getting started

Before starting any work, review `DESIGN.md`, `README.md`, and the project code.

## Running experiments

If you need to run experiments, do not use `/tmp`. Snap-confined Docker can't bind-mount from `/tmp`. Create a `.tmp` directory at the repo root and experiment there. Git is configured to ignore `.tmp`.

## Writing code and tests

Always keep your changes focused, direct, and free from overgeneralization. If you write code or tests, match the existing style as closely as possible.

Do not write unit tests; the code is not stable enough yet. If you write functional tests, adhere strictly to `tests/functional/STYLE.md`.

Run `just format` to format your code changes, including tests.

If you write GitHub workflows, make sure that `uv run zizmor .` always passes.

Always keep `DESIGN.md` current. Every aspect of the design must earn its place.

## Running tests

Make sure that `just lint` and `just unit` always pass.

You should rarely need to run the full functional test suite. Inspect how `just functional` works, then adapt the pytest command to help validate what you're working on. Beware: `just golden` is not as cheap as it looks; be selective about when you run it too.

## Committing your changes

Each commit subject must be a short, plain phrase that communicates the intent of your changes (not every detail). For example:

```
wait for the pytest tree to exit before teardown on Ctrl-C
```

House style:

- Start with a lowercase letter.
- Use present tense.
- Don't include a conventional-commit prefix such as `fix:` or `chore:`. Ignore what you see in the Git log; the commit subjects with prefixes come from PR merges.
