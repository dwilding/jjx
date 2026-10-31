# Docker hardcoding audit (support-podman checkout)

Scope: every literal or behavioral appearance of "docker" in `src/`, `tests/`,
`justfile`, `pyproject.toml`, `README.md`, `DESIGN.md`, and
`.github/workflows/`. Each row gets a verdict: **generalize** (podman must
reach it), **keep** (docker-specific by nature, podman never hits it), or
**out of scope** (true but not a runtime hardcode). Rows the podman work has
already landed are marked **resolved**, with what shipped.

The one mechanism that carries most of the rows: `_CONTAINER_BINARY` is
resolved once at import — the `JJX_RUNTIME` pin when you set it (`docker` or
`podman`, validated in preflight), else `podman` if `shutil.which("podman")`
finds it, else `docker`. Every runtime invocation — `run`, `exec`, `inspect`,
`ps`, `pull`, `rm`, `create`, `restart` — goes through it. So the "hardcode"
question only bites where docker appears *outside* that constant, or where the
two runtimes behave differently behind the same command name.

## Verdicts

### Runtime plumbing — generalized

| # | Location | Appearance | Verdict |
|---|---|---|---|
| 1 | `src/jjx/_engine.py`, `_container_binary()` | `"docker"` literal in the fallback | **keep** — this is the fallback half of the detection itself. |
| 2 | `src/jjx/_engine.py` (~20 sites) | `[_CONTAINER_BINARY, ...]` command construction | **generalized** — the switch flows through, including the from-runtime conditionals (`--time 0` in `_docker_rm`, the reachability probe). |
| 3 | `src/jjx/__init__.py` | `container_runtime()` returns `_CONTAINER_BINARY`; `http_get` and `CliError` exported | **generalized** — tests and helpers inherit the selection. |

### Image references — verified equivalent

The virtual charms pin fully-qualified registries: `docker.io/library/postgres:16`,
`docker.io/grafana/loki:3.5.5`, `docker.io/prom/prometheus:v3.5.0`,
`docker.io/grafana/grafana:12.1.0`; the charm runner is
`docker.io/ubuntu/dotnet-deps:8.0-24.04_stable`; the fixture workload uses
`ghcr.io/...`; the HTTP probe helper uses `docker.io/library/busybox:stable`.

| # | Location | Appearance | Verdict |
|---|---|---|---|
| 4 | `_virtual_postgres.py`, `_virtual_loki.py`, `_virtual_prometheus.py`, `_virtual_grafana.py`, `_engine.py` | `docker.io/...` image pins | **keep** — the registry is part of the reference; podman pulls the same manifest (verified: loki 3.5.5 pull digest matches). Not an appearance of the *runtime*. |
| 23 | `_engine.py` `_start_charm_runner`, `_http_request_on_bridge` | images from `docker.io/...` | **covered by row 4** — same reasoning; pulls verified under podman. |

### Error messages inside `CliError`s

Rows 5–8 are the remaining user-facing dishonesty: each fires on both
runtimes and names docker.

| # | Location | Appearance | Verdict |
|---|---|---|---|
| 5 | `_engine.py:790` | fallback "docker run failed" | **generalize (message)** — fires for both runtimes (stderr-empty edge); should say "container run failed". |
| 6 | `_engine.py:805` | "docker restart failed for ..." | **generalize (message)** — same path both runtimes; "container restart failed". |
| 7 | `_engine.py:883` | "unexpected docker output" | **generalize (message)** — same parsing path both runtimes; "unexpected inspect output". |
| 8 | `_engine.py:1296`, `1299` | "docker exec failed/timed out" | **generalize (message)** — same path both runtimes; "container exec failed". |
| 9 | `_engine.py:544` | docstring "failed to connect to the docker API" | **keep** — describes *pytest's* confusing docker-shaped error surfacing later; that text is docker's own either way. |
| 10 | `_engine.py`, `_check_docker_available` | preflight messages | **resolved** — preflight validates the pin (`Error: Invalid JJX_RUNTIME {value!r}: expected 'docker' or 'podman'.`) and reports the selected runtime (`Error: docker is not running. Start the docker daemon and try again.`, parameterized on `_CONTAINER_BINARY`). |
| 29 | `_engine.py`, `_docker_rm` | rm return code ignored; prints "Removed" unconditionally | **generalize (behavior)** — the mask that hid the dependent-container failure (row 25). Ordering removed the known trigger, but the mask remains: the next rm error shape would again surface as a 10-second poll expiring plus a false "Removed". Raise `CliError` with stderr on nonzero rc. |

### Documentation strings and comments — cosmetic

| # | Location | Appearance | Verdict |
|---|---|---|---|
| 11 | `_cmd_run.py` | docstring "runs the action hook via ``docker exec`` into the charm runner" | **generalize (doc)** — cosmetic; "container exec". |
| 12 | `_engine.py:1280`, `1638`, `1648`, `1820`; comments at `827-829` | "``docker exec``", "`docker inspect`", "`docker ps --all`" | **generalize (doc)** — cosmetic; the binaries are podman's when podman is selected. |

### Test corpus

| # | Location | Appearance | Verdict |
|---|---|---|---|
| 13 | `tests/functional/helpers_functional.py:41` | comment "Docker returns 1. Podman returns 125." | **keep** — it documents why the code checks `!= 0` for both runtimes; `assert_no_container` uses `jjx.container_runtime()`. |
| 14 | `test_jjx_cli_preflight.py:43` | docker-unavailable shim + "needs docker" assert | **resolved** — superseded the original recommendation. Rather than shimming both binaries, the test pins `JJX_RUNTIME=docker` so prefer-podman detection can't dodge the docker shim, and asserts the parameterized message. The symmetric podman lane landed too: `podman-unavailable/podman` shim, `@pytest.mark.podman`, pin `JJX_RUNTIME=podman`. Both tests are runtime-marked, so CI runs each on its own matrix leg. |
| 15 | `test_uid_gid.py:136` | comment "docker top" | **resolved** — the comment now reads `"<runtime> top"` and states the rootless podman constraint (row 36): `top` lists no processes for containers running as non-root users. |
| 16 | `tests/functional/charms/k8s-2-configurable/src/charm.py:67` | docstring tip "using docker inspect" | **keep** — a teaching tip about inspect under any runtime, addressed to a charm developer. |
| 17 | `conftest.py`, `cleanup_leaked_containers` | uses `jjx.container_runtime()` | **generalized** — sweeps with the selected runtime. Its rm runs at podman's default stop timeout (row 26), but only failure paths reach it, so the wait is tolerable. |
| 30 | `test_jjx_cli.py`, `test_juju_drives_containers.py`, `helpers_functional.py` | host-side fetches | **resolved** — raw-IP curls are gated on `container_ips_host_reachable()`, and `fetch_dict` + `server_up` route through `jjx.http_get`, which falls back to the busybox bridge probe (row 28). The golden-charms asserts needed no edits. |

### Infrastructure

| # | Location | Appearance | Verdict |
|---|---|---|---|
| 18 | `justfile:27` | `clean-docker` recipe, literal `docker ps ... rm` | **keep** — a developer convenience for the local snap daemon; not jjx's runtime. A `clean-podman` sibling would be fine if the dev fleet mixes runtimes. |
| 19 | `pyproject.toml:4` | description "A Juju-on-Docker test adapter for Kubernetes charms." | **out of scope** — project naming, a release decision. It is now the one claim that diverges from the README's "Docker or Podman" positioning. |
| 20 | `.github/workflows/borescope.yaml` | no `JJX_RUNTIME` pin | **resolved** — the job now pins `JJX_RUNTIME: docker` at job level, so both `jjx` steps agree. Detection prefers podman where both runtimes ship (`ubuntu-latest`), and these httpbin probes were written against Docker; the pin keeps the pre-Podman behavior until the probes are checked under podman. The sibling workflows matrix (functional) or pin (golden) instead. |
| 21 | `README.md`, `DESIGN.md` | title, install, group-setup advice, demo | **resolved** — the title is "Spin up K8s charms on Docker or Podman", install covers both runtimes, the prefer-podman rationale is documented, the docker-group advice stays (it is about the docker daemon when docker is the one selected), and the demo is split: a Docker block curling raw container IPs, a Podman block publishing `8000:8000` and curling `127.0.0.1`. DESIGN.md's runtime model now states the pin, the prefer-podman rationale, the reachability divergence, and the runner-45 teardown order. |
| 22 | `README.md` | hub.docker.com image links | **keep** — links, not runtime behavior. |

### Charm code contract surfaces

| # | Location | Appearance | Verdict |
|---|---|---|---|
| 24 | `_docker_container_details` inspect template | `{{.NetworkSettings.Networks}}` docker-shaped template | **verified** — podman 4.9.3 populates the path when the container runs `--network bridge`, and all five `_docker_run` sites now pass it explicitly (row 27), so empty-IP does not occur under jjx's invocation shape. The original scare was a slirp4netns default-network probe, not jjx's code path. |

## Runtime divergences (rows 25–36)

Behaviors, not literals: places where the two runtimes genuinely differ
behind the same command, found while landing the support. Rows 31–34 carry
the rootless-podman impact points from the root-question plan
(JJX_PLAN_NEW.md, "Impact on future rootless Podman") verified against this
checkout.

| # | Divergence | Resolution |
|---|---|---|
| 25 | **Teardown ordering.** `podman rm --force` on a workload fails instantly (rc 125, "has dependent containers") while its charm runner (`--network=container:<workload>`) exists. Docker removes it anyway (rc 0, 0.24s). | **resolved** — charm runner before workload at every teardown site: `destroy_model` priorities (runner 45, workload 50), `remove_application`, deploy's except path, deploy's stale-app cleanup. |
| 26 | **rm stops before removing.** `podman rm --force` stops the container first and waits out the full stop timeout (10s) on a SIGTERM-ignoring container. `docker rm -f` SIGKILLs. | **resolved** — `_docker_rm` passes `--time 0` under podman. Measured: 10.15s falls to 0.13s, matching docker's 0.24s. The flag is podman-only (`docker rm -f --time 0` errors "unknown flag"), so it rides an `if _CONTAINER_BINARY == "podman"` branch. |
| 27 | **Default container network.** Rootless podman `run` defaults to slirp4netns: an isolated netns whose IPs nothing else reaches. Docker defaults to bridge. | **resolved** — the four virtual charms pass `network="bridge"` explicitly (bridge peers reachable, podman assigns a 10.88.x IP); the workload already passed it. |
| 28 | **Host reachability.** Under rootless podman, container IPs are unreachable from the host. Docker and rootful podman expose a host bridge, so both reach them. | **resolved** — `container_ips_host_reachable()` (docker ⇒ true, else a rootless probe, cached) routes `http_get`/`http_post`: host urlopen when reachable, else a short-lived busybox wget on the bridge network. `_wait_for_http` wraps the poll for COS readiness, and CLI output gains the publish advice under rootless podman. |
| 29 | **`_docker_rm` masks failures.** The return code is ignored, and after the 10s poll the function prints "Removed" unconditionally. | **open** — the ordering fix removed the known trigger (this mask is what hid it), but the next rm error would hide the same way. |
| 31 | **UID map coverage (170).** The charm runner runs as `170:170`; the workload may declare any `uid`/`gid`, and undeclaring workloads run at the image's user. Rootless podman maps container IDs into the subuid/subgid allocations. | **resolved** — 170 falls inside even the *default* allocation (100000–165535), so the runner maps cleanly and bind-mount files come out host-owned. Verified live: runner at 170:170 works under this host's rootless podman. The one surface to watch is a workload declaring a uid or gid outside the allocations — jjx passes it straight to `--user`, and podman refuses at run, not pull. Not predictable from jjx's side; the run error names the value. |
| 32 | **`_shared_dir_group()`** (plan's bullet: "the group-access mechanism changes") | **resolved** — `_shared_dir_group()` returns container group `"0"` under podman (container group 0 maps to the host user's own group under rootless mapping), else `os.getgid()` under docker. Rides the `_docker_run` `group_add` parameter (charm runner, `_cmd_deploy.py:357`). Under docker, host GID; under rootless podman, group 0. Same host-owned outcome. |
| 33 | **Rocks chowned to 584792 (`_daemon_`).** Ubuntu rocks symlink/chown files to UID 584792 (`_daemon_`); rootless podman fails to unpack the image when 584792 falls outside `/etc/subuid`+`/etc/subgid` — at `pull`/`load`, with a confusing error ("potentially insufficient UIDs or GIDs", lchown: invalid argument). The plan called for catching this at image pull and printing the fix (extend subuid/subgid to cover 584792, then `podman system migrate`). | **half-resolved (docs + CI).** README's "Podman user mapping" paragraph covers the default-allocation gap and the fix, and the podman CI leg applies it (`sed` the runner's lines to `100000:1000001`, then `podman system migrate`) so the `charm_user` rock-deploying tests pass there. Still open in code: `_docker_pull_with_retry` doesn't catch the failure shape and print the fix. Two shapes to match at pull/run: (a) "insufficient UIDs/GIDs … Check /etc/subuid and /etc/subgid … podman system migrate" (rockcraft#911); (b) "container uses ID mappings … doesn't map UID 584792" (rockcraft#873). jjx's own fixture image (`ghcr.io/canonical/api_demo_server/api-demo-server`, user 584792) hits this on a stock machine. **Not reproducible on this host**: subuid/subgid were already extended (100000–1100000), covering 584792. Confirmed live on CI: the podman leg's `charm_user` rock tests failed on stock allocation before the widening step existed. |
| 34 | **Resolution rules from the root-questions plan need no change** (plan's bullet 4, see /home/david.wilding@canonical.com/gh/jjx/dwilding-root-question/JJX_PLAN_NEW.md) | **resolved** — the podman support preserved the full `charm-user`/`uid`/`gid` matrix (170:170 runner always, workload follows declarations, 1000–9999 rejected). Verified consistent with DESIGN.md's charm-runner section. |
| 35 | **Inner charm tests fetch container IPs from the host.** The fastapi-demo integration tests urlopen the workload and Loki IPs directly, with no timeout on the Loki fetch (row 28's reachability gap from the *test's* side). Under rootless podman the SYN black-holes, so `test_loki_data` hangs forever instead of failing. jjx can't fix upstream test code. | **resolved (markers)** — the two fastapi golden tests are `@pytest.mark.docker`: they depend on host-reachable container IPs, which docker provides and rootless podman cannot. The other golden tests stay unmarked and run under both. CI additionally widens the podman runner's subuid allocation (row 33) so the `charm_user` rock-deploying tests pass on the podman leg. |
| 36 | **Rootless Podman `top` lists no processes for non-root container users.** `<runtime> top <c> -o pid,uid,args` is how `test_uid_gid.py` reads the service UID. Docker runs ps on the host, so it lists any container's processes. Rootless podman only lists containers whose processes map to the host user: `test_uid_gid_zero_runs_as_root` (`--user 0:0`) gets its uvicorn row; the image-declared user 584792 (mapped to a host UID that is not the runner's) gets an empty table, rc 0. The rock is distroless, so no `ps`/`sh` fallback via exec either. | **resolved (markers)** — `test_no_uid_gid_runs_as_image_user` is `@pytest.mark.docker`; `test_uid_gid_zero_runs_as_root` stays unmarked, verified live under rootless podman (this host) and on the CI leg. `_process_uid`'s comment states the constraint. |

## Prioritized recommendations

1. **Should-fix (honesty):** row 29 — raise `CliError` when `rm --force`
   fails; rows 5–8 — parameterize the four `docker X failed` strings;
   row 33 — catch the 584792 unpack failure at pull and print the subuid
   fix, the plan's one unlanded landmine (the README guidance half landed;
   the pull-time catch is what remains).
2. **Cosmetic (do opportunistically, zero-risk):** rows 11, 12.
3. **Keep as docker-specific:** rows 1, 4, 9, 13, 16, 17, 18, 22.
4. **Decide later:** row 19 (the package description now diverges from the
   README), a podman probe under `@pytest.mark.podman` beyond the preflight
   shims (worth adding when a podman-only behavior needs coverage the
   unmarked suite can't give), and broadening README's
   Podman caveat once the pull-time catch lands (row 33).

Landed this session: the runtime marker layer (`pyproject.toml` markers,
`conftest.py` skip hook defaulting to `docker`), the `podman-unavailable`
shim lane (row 14), the `functional.yaml` matrix over both runtimes, the
`golden-charms.yaml` docker pin (a weekly cost call), the borescope pin
(row 20), and the justfile `JJX_RUNTIME` default so local runs pin docker
unless the ambient variable overrides. From the PR's first CI run: the
`@pytest.mark.docker` marks on the two fastapi golden tests (their inner
suites urlopen container IPs, which rootless podman black-holes — row 35,
confirmed live against `test_loki_data`), the podman CI leg's subuid
widening step (row 33, confirmed by the leg's `charm_user` failures on
stock allocation), and the `@pytest.mark.docker` mark on
`test_no_uid_gid_runs_as_image_user` (row 36: rootless podman `top`
returns an empty table for containers running as non-root users,
reproduced live on this host with the rock and with busybox).

## What I verified live

Everything under rootless podman ran as this host's own user, podman 4.9.3.

- CLI suite on the k8s-2-configurable charm: 9 passed in 117s, including the
  `test_uvx_jjx` teardown the ordering bug (row 25) used to fail.
- `test_juju_drives_containers.py`: 6 passed in 15s, with `server_up`
  exercising the busybox bridge probe (row 28).
- Full `jjx -d` on the k8s-2 charm under rootless podman: 2/2 integration
  tests, runner at 170:170, workload serving HTTP 200 from the runner netns,
  clean teardown.
- Docker-only PATH (podman unresolvable): the same suites pass — the
  fallback detection path is intact.
- `docker.io/grafana/loki:3.5.5` pull: the manifest digest matches Docker Hub's.
- rm timing (SIGTERM-ignoring container): podman default 10.15s, podman
  `--time 0` 0.13s, docker `rm -f` 0.24s; `docker rm -f --time 0` rejects
  the flag.
- Dependent-container refusal: podman rc 125 with an instant message;
  docker rc 0.
- `--network bridge` combined with `-p 127.0.0.1:PORT:PORT`: accepted by
  rootless podman; the host reaches the published port; bridge peers reach
  each other (busybox wget full-body fetch).
- `--network container:<name>`, `--restart unless-stopped`, and
  `--group-add 0`: all accepted under rootless podman, with group 0 mapping
  to the host user's own group.
- `just format`, `just lint`, `just unit` (3 passed), and `uv run zizmor .`
  on the workflow pins: all green.
