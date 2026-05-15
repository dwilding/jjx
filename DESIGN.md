# jjx design

`jjx` exists for one job: run integration tests for a small Kubernetes charm from local source with real `ops` and real Pebble, without a Juju controller.

The UX target is minimal:

`uv run --group integration --with jjx pytest -v tests/integration`

Before running the integration tests, create a dummy charm artifact in the charm project
directory, for example:

`touch placeholder.charm`

The test fixture only needs a `.charm` file to exist; `jjx` treats it as a deploy
trigger and does not unpack it.

No project dependency changes. No `jjx` configuration.
This command injects only `jjx`; it assumes the project already provides its normal test dependencies.

## scope

Supported:

- single application
- single unit (`app/0`)
- deploy via a `.charm` argument interpreted as a trigger to run local `./src`
- config updates and status reporting
- hook tools needed by the charm
- real Pebble in Docker

Not supported:

- relations
- peers or subordinates
- multi-unit behavior
- controller features beyond this test niche

If a charm needs any of the above, use real Juju.

## runtime model

`jjx` is a short-lived CLI. Each command starts, reads or updates state, performs work, and exits.

State is local to the project working directory.

Charm code is executed from `./src/` with the Python interpreter inherited from the outer `uv run` process.

The `.charm` file passed to deploy is a trigger only. `jjx` does not inspect or extract it.

## filesystem contract

`jjx` writes project-local state to `./.jjx/`:

- `./.jjx/.gitignore`
- `./.jjx/state.json`
- `./.jjx/hook-tools/`
- `./.jjx/pebble/` (Pebble runtime directory)
- `./.jjx/charm/` (staged runtime charm directory with `src/`, `metadata.yaml`, `config.yaml`, and `.unit-state.db`)

`jjx` also caches the Pebble binary at `~/.cache/jjx/pebble-bin`, downloaded from canonical/pebble GitHub Releases on first use. This cache is shared across projects and persists across model teardowns to enable reuse across multiple deployments.

Notes on generated runtime files:

- Pebble runtime files (for example `socket`, `.pebble.state`, and `identity/key.pem`) are created under `./.jjx/pebble/`.
- `./.jjx/charm/.unit-state.db` is created by charm runtime state persistence.

When the model is torn down, jjx removes the entire `./.jjx/` directory. The `~/.cache/jjx/pebble-bin` cache is kept for reuse across subsequent deployments.

The socket path is intentionally short to reduce Unix socket path-length risk.
Very long working-directory paths can still exceed platform limits.

## execution contract

Exact sequence:

1. user runs `uv run --group integration --with jjx pytest -v tests/integration`
2. `uv` prepares an environment with test dependencies and `jjx`
3. `pytest` (via `jubilant`) invokes `juju ...` commands
4. those commands execute `jjx` in that same `uv` environment
5. for hook events, `jjx` launches `bubblewrap`
6. inside `bubblewrap`, `jjx` runs `src/charm.py` using `sys.executable`
7. `sys.executable` comes from the outer `uv` environment (no nested `uv run` per hook)
8. hook tools execute `jjx` subcommands via that same `sys.executable`
9. charm code interacts with hook tools and Pebble, then exits; `jjx` persists resulting state

Deploy flow:

1. ensure `./.jjx` exists and load state
2. stage runtime charm files in `./.jjx/charm/` (`src/`, `metadata.yaml`, `config.yaml`)
3. start workload container and Pebble
4. bind host socket at `./.jjx/socket`
5. run charm hooks in `bubblewrap`
6. serve hook tools from `./.jjx/hook-tools`
7. persist resulting app and unit status

Config flow:

1. update state
2. run `config-changed` hook
3. persist resulting status

Destroy flow:

1. stop container
2. remove `./.jjx/socket`
3. remove state

## behavior guarantees

- real `ops` framework (`ops` 3.x)
- real Pebble API surface through Unix socket
- hook tools invoked as subprocess executables
- synchronous event execution (no queue, no background agent)
- deterministic single-unit semantics

## constraints

- requires Docker
- requires Docker socket access from the calling shell (for example via docker group membership)
- requires `bubblewrap`
- requires Linux (Unix sockets + `bubblewrap` assumptions)
- assumes charm source is present in `./src`
- assumes charm metadata files are present in project

These constraints are deliberate. They keep the system small, predictable, and fast to debug.

`bubblewrap` decision note: we considered dropping hook-process isolation because charm code is typically trusted in local development. We are keeping `bubblewrap` for now to reduce accidental host-side effects and improve test reproducibility across machines. This remains a conscious tradeoff, not a permanent rule; we can revisit if operational simplicity becomes more valuable than the isolation benefits.

## design intent

This is not a fake Juju platform. It is a focused test adapter.

Every feature must justify itself against one question: does this help single-unit local charm integration tests run with high fidelity and low complexity?

If not, it does not belong.
