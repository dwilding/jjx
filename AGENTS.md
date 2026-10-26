# AGENTS.md

Before starting any work, review `DESIGN.md`, `README.md`, and the project code.

If you need to run experiments, do not use `/tmp`. Snap-confined Docker can't bind-mount from `/tmp`. Create a `.tmp` directory at the repo root and experiment there. Git is configured to ignore `.tmp`.

If you write code or tests, match the existing style as closely as possible. Do not write unit tests; the code is not stable enough yet. If you write functional tests, adhere strictly to `tests/functional/STYLE.md`.

Run `just format` to format your code changes, including tests. Make sure that `just lint` and `just unit` always pass.

You should rarely need to run the full functional test suite. Inspect how `just functional` works, then adapt the pytest command to help validate what you're working on. Beware: `just golden` is not as cheap as it looks; be selective about when you run it too.

If you write GitHub workflows, make sure that `uv run zizmor .` always passes.

Always keep your changes focused, direct, and free from overgeneralization.

Always keep `DESIGN.md` current. Every aspect of the design must earn its place.

If you commit your changes, the commit message should be a short, plain, present tense phrase that communicates the intent of your changes (not every detail). Don't include a conventional-commit prefix.
