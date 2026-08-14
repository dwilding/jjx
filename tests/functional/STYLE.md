# Functional test style

These conventions keep functional tests scannable and consistent. Follow them when writing or editing tests in this directory.

## Test functions

- No docstrings on test functions.
- Use body comments to mark the sequence of steps. Short separate sentences. No parentheticals or semicolons.
- If a comment needs a second sentence for context, put it on its own line.
- Use `# TEARDOWN` to mark the start of the cleanup section at the end of a test.
- No pre-test cleanup. The `cleanup_leaked_containers` fixture in `conftest.py` removes leftover containers at the end of the session, and each test's TEARDOWN section handles its own cleanup.

```python
def test_something(k8s_2_configurable):
    # Deploy the app.
    ...
    # TEARDOWN
    command = [
        *JUJU,
        "remove-application",
        "fastapi-demo",
    ]
    subprocess.run(
        command,
        cwd=k8s_2_configurable,
        check=True,
    )
```

## Teardown robustness

- Wrap the body of a test in `try/finally` when it deploys containers.
- Put the `# TEARDOWN` section inside the `try` block. It runs on the happy path.
- The safety-net cleanup in `finally` depends on how the test runs jjx:
  - `jjx -d` (blocking `subprocess.run`): the process has already exited. Put a `jjx down` call in `finally`. It uses `check=False` and `capture_output=True`. It must not raise.
  - `jjx` (long-running `Popen`): the process is still alive. Put `proc.kill()` in `finally`. Killing the process triggers jjx's signal handler, which tears down containers.
- Do not assert on the safety-net call's output. Assertions belong in the TEARDOWN section.

```python
def test_something(temp_dir):
    ...
    result = subprocess.run(
        command,
        cwd=charm_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        assert result.returncode == 0, ...
        ...
        # TEARDOWN
        command = [...]
        result = subprocess.run(
            command,
            cwd=charm_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, ...
    finally:
        # Safety net for jjx -d: run jjx down in case an assertion
        # failed before TEARDOWN completed.
        command = [...]
        subprocess.run(
            command,
            cwd=charm_dir,
            capture_output=True,
            text=True,
            check=False,
        )
```

For long-running `jjx` (Popen), the safety net kills the process instead:

```python
def test_something(k8s_2_configurable):
    ...
    proc = subprocess.Popen(
        command,
        cwd=k8s_2_configurable,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        ...
        # TEARDOWN
        proc.send_signal(signal.SIGINT)
        assert proc.wait(timeout=30) == 130
    finally:
        # Safety net for jjx (Popen): kill the process if it's still
        # running. This triggers jjx's signal handler to tear down.
        if proc.poll() is None:
            proc.kill()
```

- The `cleanup_leaked_containers` fixture in `conftest.py` is a session-wide safety net. It runs once at the end of the test session and removes any leftover `jubilant-*`, `jjx-default-*`, or `jjx-layer-copy-*` containers. Tests should still clean up after themselves. The fixture prevents leaked containers from accumulating across runs. It is session-scoped (not function-scoped) because some test files share a deployed container across multiple tests in a sequence.

## Subprocess calls

- Always build a `command = [...]` list, then call `subprocess.run(command, ...)`.
- Name the result `result`.
- Use `capture_output=True, text=True, check=False` when asserting on the return code or output. `check=False` lets the test inspect the failure rather than raising `CalledProcessError`, which would lose the captured output.
- Use `check=True` (no `capture_output`) when output isn't needed and a failure should raise.

```python
command = [
    *JUJU,
    "deploy",
    "./placeholder.charm",
    "fastapi-demo",
    "--resource",
    "demo-server-image=ghcr.io/canonical/api_demo_server/api-demo-server:2.1.0",
]
subprocess.run(
    command,
    cwd=k8s_2_configurable,
    check=True,
)
```

## Asserts

- Assert messages are only for return-code checks where success is expected. Include stdout/stderr for debugging.

```python
assert result.returncode == 0, (
    f"jjx exited with code {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
)
```

- Content and state asserts have no custom message.

```python
assert "Grafana" in result.stdout
assert f"Removed {container_name}" in result.stdout
assert not (charm_dir / ".jjx").exists()
```

- Return-code asserts that expect a specific failure code have no message. The output isn't needed for debugging because the failure is expected.

```python
assert result.returncode == 1
assert result.returncode != 0
assert proc.wait(timeout=30) == 130
```

## Deriving values from output

- When a test needs the model name (e.g., to build container names), parse it from `jjx` stdout:

```python
model_name = result.stdout.split("--juju-model ")[1].split()[0]
container_names = [
    f"{model_name}-test-charm-operator",
    f"{model_name}-test-charm-demo-server",
]
```

## Assert ordering

After a `subprocess.run`, assert in this order:

1. Return code.
2. Output content. stdout first, then stderr.
3. Values derived from output (e.g., model name, container names).
4. Container state. `helpers.assert_container` / `helpers.assert_no_container`.
5. Behavioral checks (e.g., HTTP endpoints, Loki/Prometheus/Grafana configs).
6. Filesystem state (e.g., `.jjx` exists or is gone, `placeholder.charm`).

The same order applies after `jjx down`: return code, then `Removed` lines in stdout, then containers are gone, then `.jjx` is gone.

## Polling loops

- Use `while`/`else` with a `time.monotonic()` deadline.
- Terse `raise AssertionError("...")` in the `else` block. No stdout/stderr.

```python
deadline = time.monotonic() + 30.0
while time.monotonic() < deadline:
    try:
        helpers.assert_container(CONTAINER_NAME)
    except subprocess.CalledProcessError:
        time.sleep(0.5)
        continue
    break
else:
    raise AssertionError("container did not start")
```

## Fixtures and helpers

- Use the `k8s_2_configurable` fixture for the stored charm. No Git clone.
  - This fixture copies `tests/functional/charms/k8s-2-configurable` into a temp dir and returns the path. Use it for tests that drive `juju` commands or `jjx` directly.
  - The `temp_dir` fixture (module-scoped) is for tests that clone a charm from Git (see `test_golden_charms.py`). Prefer `k8s_2_configurable` when the stored charm suffices.
- Use `helpers.assert_container` / `helpers.assert_no_container` for container checks.
- Module-level constants for reused values:
  - `PACKAGE_DIR = pathlib.Path(__file__).parent.parent.parent` — the repo root, used in `uvx --with-editable` and `JUJU` commands.
  - `JUJU = ["uv", "run", "--group", "integration", "--with-editable", PACKAGE_DIR, "juju"]` — the `juju` shim, for tests that call `juju` commands directly (not the full `jjx` CLI).
  - `CONTAINER_NAME = "jjx-default-demo-server"` — the workload container name for the stored charm.

## Running jjx vs juju commands

- Use `uvx --with-editable PACKAGE_DIR jjx ...` to run the full `jjx` CLI (e.g., `jjx -d`, `jjx down`).
- Use the `JUJU` constant to run individual `juju` commands (e.g., `deploy`, `wait-for`, `config`, `remove-application`) when testing specific juju shim behavior.
- Both are run via `subprocess.run` with the `command`/`result` pattern.

## Signal and interrupt tests

- `subprocess.Popen` with `start_new_session=True` is acceptable when the test needs to send a signal to the process group (e.g., simulating Ctrl-C via `os.killpg`).
- This is the only case where `Popen` is used instead of `subprocess.run`.
