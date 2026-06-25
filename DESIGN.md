# jjx design

`jjx` exists for one job: run integration tests for a small Kubernetes charm from local source with real `ops` and real Pebble, without a Juju controller.

## the `jjx` command

The primary interface is the `jjx` command, run from the charm project directory:

```
jjx          # run tests, then wait (Ctrl-C to tear down)
jjx -d       # run tests, then detach (container stays up; use 'jjx down' to tear down)
jjx down     # tear down all models and remove runtime state
```

Optional flags:

- `-p HOST:CONTAINER` — publish a container port to `127.0.0.1:HOST`

`jjx` handles the full lifecycle: preflight cleanup of any stale state, creating and removing the placeholder charm artifact, invoking pytest, and tearing down on exit.

## customization

Pytest arguments can be overridden in the charm project's `pyproject.toml`:

```toml
[tool.jjx]
pytest-args = ["-v", "tests/integration", "--no-juju-teardown"]
```

If `pytest-args` is not set, `jjx` defaults to `["tests/integration", "--no-juju-teardown"]`.

## under the hood

`jjx` invokes pytest via:

```
uv run --group integration [--with jjx | --python <venv>] pytest <pytest-args>
```

The test fixture only needs a `.charm` file to exist; `jjx` creates one automatically before running pytest and removes it afterwards. `jjx` treats it as a deploy trigger and does not unpack it.

No project dependency changes are required. `jjx` injects itself and assumes the project already provides its normal test dependencies.

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
- `./.jjx/sitecustomize.py` (runtime Python shim injected into charm hook execution)
- `./.jjx/charm/` (staged runtime charm directory with `src/`, `metadata.yaml`, `config.yaml`, and `.unit-state.db`)

`jjx` also caches the Pebble binary at `~/.cache/jjx/pebble-bin`, downloaded from canonical/pebble GitHub Releases on first use. This cache is shared across projects and persists across model teardowns to enable reuse across multiple deployments.

Notes on generated runtime files:

- Pebble runtime files are created inside the workload container under Pebble's default state path: `/var/lib/pebble/default`.
- `./.jjx/socket` is a host-side bind target for the Pebble API socket, used to bridge the containerized Pebble daemon to host-side hook execution.
- `JJX_CONTAINER_IP` is injected into the hook process environment from Docker inspect output for the workload container.
- `JJX_STATE_DIR` is injected into the hook process environment pointing at the `.jjx/` directory. Inside `bubblewrap`, the charm's cwd is `/charm`, so hook tools (which call back into `jjx` to read and write state) cannot discover `.jjx/` by walking up from cwd. This env var lets them locate state directly.
- `./.jjx/sitecustomize.py` rewrites outbound Python socket connects from `0.0.0.0:<port>` to the workload container bridge IP with the same port.
- `./.jjx/charm/.unit-state.db` is created by charm runtime state persistence (written by `ops` via `sqlite3` to `JUJU_CHARM_DIR/.unit-state.db`, which inside `bubblewrap` is `/charm/.unit-state.db`).

When the model is torn down, jjx removes the entire `./.jjx/` directory. The `~/.cache/jjx/pebble-bin` cache is kept for reuse across subsequent deployments.

The socket path is intentionally short to reduce Unix socket path-length risk.
Very long working-directory paths can still exceed platform limits.

### bubblewrap and the `/charm` filesystem

`bubblewrap` serves two purposes: filesystem isolation and filesystem fidelity.

For **fidelity**, `bubblewrap` bind-mounts the staged charm directory (`./.jjx/charm/`) to `/charm` inside the sandbox. This matches real Juju, where the charm directory *is* `/charm` and `JUJU_CHARM_DIR=/charm`. Charms that hardcode `/charm` paths for file I/O (not just the Pebble socket) work correctly because `/charm` is a real directory they can read and write. `JUJU_CHARM_DIR` is set to `/charm`, so `ops` writes `.unit-state.db` to `/charm/.unit-state.db` — the same path real Juju uses.

For **isolation**, `bubblewrap` gives the charm process a restricted filesystem view: a tmpfs root with read-only system directories (`/usr`, `/bin`, `/lib`, `/lib64`, `/etc`, `/home`) and write access only to `/charm`, the project root, and `/tmp`. This prevents accidental host-side writes and hides host-specific paths that could reduce test reproducibility across machines.

`sitecustomize.py` handles a complementary concern that `bubblewrap` cannot: network path fidelity. It rewrites outbound TCP connects from `0.0.0.0:<port>` to the workload container bridge IP, so charm code that binds to `0.0.0.0` reaches the container without exposing ports on the host. `bubblewrap` is a filesystem sandbox, not a network proxy, so these two mechanisms are orthogonal and both necessary.

## execution contract

Exact sequence:

1. user runs `uv run --group integration --with jjx pytest -v tests/integration`
2. `uv` prepares an environment with test dependencies and `jjx`
3. `pytest` (via `jubilant`) invokes `juju ...` commands
4. those commands execute `jjx` in that same `uv` environment
5. for hook events, `jjx` launches `bubblewrap`
6. inside `bubblewrap`, `jjx` runs `/charm/src/charm.py` using `sys.executable`
7. `sys.executable` comes from the outer `uv` environment (no nested `uv run` per hook)
8. hook tools execute `jjx` subcommands via that same `sys.executable`
9. charm code interacts with hook tools and Pebble, then exits; `jjx` persists resulting state

Deploy flow:

1. ensure `./.jjx` exists and load state
2. stage runtime charm files in `./.jjx/charm/` (`src/`, `metadata.yaml`, `config.yaml`)
3. start workload container and Pebble on Docker bridge networking (no host networking)
   - if `JJX_DOCKER_PUBLISH` is set to `HOST_PORT:CONTAINER_PORT`, add Docker publish `127.0.0.1:HOST_PORT:CONTAINER_PORT`
4. bind host socket at `./.jjx/socket`
5. resolve workload container IP
6. set `JJX_CONTAINER_IP` in hook process environment from the resolved container IP
7. write `./.jjx/sitecustomize.py` and prepend `./.jjx` to hook `PYTHONPATH`
8. serve hook tools from `./.jjx/hook-tools`
9. run charm hooks in `bubblewrap`
10. persist resulting app and unit status

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
- charm code that connects to `0.0.0.0:<port>` reaches the workload container without exposing container ports on the host

## constraints

- requires Docker
- requires Docker socket access from the calling shell (for example via docker group membership)
- requires `bubblewrap`
- requires Linux (Unix sockets + `bubblewrap` assumptions)
- assumes charm source is present in `./src`
- assumes charm metadata files are present in project

State isolation rule:
- `./.jjx/state.json` is internal runtime state and not a supported charm interface.
- Charm code must not read or write `./.jjx/state.json`; runtime behavior must not depend on charm access to this file.

These constraints are deliberate. They keep the system small, predictable, and fast to debug.

`bubblewrap` decision note: we considered dropping hook-process isolation because charm code is typically trusted in local development. We are keeping `bubblewrap` because it provides both filesystem fidelity (the charm sees `/charm` as a real directory matching real Juju, so charms that hardcode `/charm` paths work correctly) and filesystem isolation (preventing accidental host-side writes and hiding host-specific paths that could reduce test reproducibility across machines). The fidelity benefit cannot be replicated by Python-level interception alone, because `ops` writes state via `sqlite3` (a C module that bypasses Python file APIs) and charms may interact with the `/charm` filesystem directly. This remains a conscious tradeoff, not a permanent rule; we can revisit if operational simplicity becomes more valuable than the fidelity and isolation benefits.

## design intent

This is not a fake Juju platform. It is a focused test adapter.

Every feature must justify itself against one question: does this help single-unit local charm integration tests run with high fidelity and low complexity?

If not, it does not belong.
