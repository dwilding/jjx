import pathlib
import shutil
import subprocess

import pytest

import jjx

PACKAGE_DIR = pathlib.Path(__file__).parent.parent.parent
CHARMS_DIR = pathlib.Path(__file__).parent / "charms"
JUJU = [
    "uv",
    "run",
    "--group",
    "integration",
    "--with-editable",
    PACKAGE_DIR,
    "juju",
]
CHARM_RUNNER_NAME = "jjx-default-operator"
DEPLOY = [
    "deploy",
    "./placeholder.charm",
    "fastapi-demo",
    "--resource",
    "demo-server-image=ghcr.io/canonical/api_demo_server/api-demo-server:2.1.0",
]


@pytest.fixture
def charm_dir():
    # Copy the charm under the package tree, not /tmp (see AGENTS.md). Each test
    # mutates charmcraft.yaml, so it needs its own copy.
    root = PACKAGE_DIR / ".tmp" / "test_charm_user"
    shutil.rmtree(root, ignore_errors=True)
    charm = root / "k8s-2-configurable"
    shutil.copytree(
        CHARMS_DIR / "k8s-2-configurable",
        charm,
        ignore=shutil.ignore_patterns(".*", "_*", "*.charm"),
    )
    return charm


def read_charm_user(charm_dir: pathlib.Path) -> str | None:
    text = (charm_dir / "charmcraft.yaml").read_text()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("charm-user:"):
            return stripped.split(":", 1)[1].strip()
    return None


def set_charm_user(charm_dir: pathlib.Path, value: str) -> None:
    path = charm_dir / "charmcraft.yaml"
    text = path.read_text()
    lines = text.splitlines(keepends=True)
    out = []
    replaced = False
    for line in lines:
        if line.strip().startswith("charm-user:"):
            out.append(f"charm-user: {value}\n")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.insert(0, f"charm-user: {value}\n")
    path.write_text("".join(out))


def remove_charm_user(charm_dir: pathlib.Path) -> None:
    path = charm_dir / "charmcraft.yaml"
    text = path.read_text()
    lines = [
        line
        for line in text.splitlines(keepends=True)
        if not line.strip().startswith("charm-user:")
    ]
    path.write_text("".join(lines))


def assert_runner_user(container_name: str, expected: str) -> None:
    runtime = jjx.container_runtime()
    result = subprocess.run(
        [
            runtime,
            "inspect",
            "--format",
            "{{.Config.User}}",
            container_name,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"'{runtime} inspect' exited with code {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    user = result.stdout.strip()
    assert user == expected, f"charm runner user is {user!r}, expected {expected!r}"


def deploy(charm_dir: pathlib.Path) -> subprocess.CompletedProcess[str]:
    (charm_dir / "placeholder.charm").touch()
    command = [*JUJU, *DEPLOY]
    return subprocess.run(
        command,
        cwd=charm_dir,
        capture_output=True,
        text=True,
        check=False,
    )


def remove_app(charm_dir: pathlib.Path) -> None:
    command = [
        *JUJU,
        "remove-application",
        "fastapi-demo",
    ]
    subprocess.run(
        command,
        cwd=charm_dir,
        capture_output=True,
        text=True,
        check=False,
    )


def run_jjx(charm_dir: pathlib.Path, *args: str) -> subprocess.CompletedProcess[str]:
    # Run the full jjx CLI the way a user does. Detach (-d) so jjx exits after
    # the run instead of waiting for Ctrl-C.
    command = [
        "uvx",
        "--with-editable",
        PACKAGE_DIR,
        "jjx",
        "-d",
        *args,
    ]
    return subprocess.run(
        command,
        cwd=charm_dir,
        capture_output=True,
        text=True,
        check=False,
    )


def jjx_down(charm_dir: pathlib.Path) -> subprocess.CompletedProcess[str]:
    command = [
        "uvx",
        "--with-editable",
        PACKAGE_DIR,
        "jjx",
        "down",
    ]
    return subprocess.run(
        command,
        cwd=charm_dir,
        capture_output=True,
        text=True,
        check=False,
    )


def test_charm_user_non_root_runs_170(charm_dir):
    # Backstop: the stored charm declares non-root.
    assert read_charm_user(charm_dir) == "non-root"
    # Deploy the app.
    result = deploy(charm_dir)
    try:
        assert result.returncode == 0, (
            f"juju deploy exited with code {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        # The non-root row is silent: no jjx warning on stderr. (uv prints its
        # own unrelated warnings, so match the message, not the word.)
        assert "charm-user is not set" not in result.stderr
        # The charm runner runs as 170:170.
        assert_runner_user(CHARM_RUNNER_NAME, "170:170")
        # TEARDOWN
        remove_app(charm_dir)
    finally:
        # Safety net: remove the app in case an assertion failed
        # before TEARDOWN completed.
        remove_app(charm_dir)


def test_charm_user_root_fails(charm_dir):
    # Change the declaration to root.
    set_charm_user(charm_dir, "root")
    # Deploy fails.
    result = deploy(charm_dir)
    assert result.returncode != 0
    assert "charm-user 'root' is not supported" in result.stderr
    # No containers were created.
    import helpers_functional as helpers

    helpers.assert_no_container(CHARM_RUNNER_NAME)


def test_charm_user_sudoer_fails(charm_dir):
    # Change the declaration to sudoer.
    set_charm_user(charm_dir, "sudoer")
    # Deploy fails.
    result = deploy(charm_dir)
    assert result.returncode != 0
    assert "charm-user 'sudoer' is not supported" in result.stderr
    # No containers were created.
    import helpers_functional as helpers

    helpers.assert_no_container(CHARM_RUNNER_NAME)


def test_charm_user_root_rejected_by_cli_preflight(charm_dir):
    # Change the declaration to root.
    set_charm_user(charm_dir, "root")
    # Run the full jjx CLI. Preflight rejects 'root' before pytest starts,
    # so the user sees one clean error instead of deploy-time tracebacks.
    result = run_jjx(charm_dir)
    assert result.returncode == 1, (
        f"jjx exited with code {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "Error: charm-user 'root' is not supported" in result.stderr
    assert "jjx expects rootless charm code" in result.stderr
    # No test session ran, so no state was created.
    assert "--juju-model " not in result.stdout
    assert not (charm_dir / ".jjx").exists()
    assert not (charm_dir / "placeholder.charm").exists()


def test_charm_user_unset_warns_and_runs_170(charm_dir):
    # Remove the declaration.
    remove_charm_user(charm_dir)
    # Run the full jjx CLI. The warning prints at preflight, on stderr, where
    # pytest's capture of the deploy subprocess would otherwise hide it.
    result = run_jjx(charm_dir, "--", "-k", "test_deploy")
    try:
        assert result.returncode == 0, (
            f"jjx exited with code {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert "Warning: charm-user is not set" in result.stderr
        assert "jjx expects rootless charm code" in result.stderr
        # The warning ends with a blank line before pytest's output starts.
        assert "Set 'charm-user: non-root' in charmcraft.yaml.\n\n" in result.stderr
        # pytest-jubilant names its model jubilant-<hex>-test-charm, so the
        # charm runner is named after that model. Parse the model from the
        # hint jjx prints after the test session.
        model_prefix = result.stdout.split("--juju-model ")[1].split()[0]
        # The charm runner still runs as 170:170.
        assert_runner_user(f"{model_prefix}-test-charm-operator", "170:170")
        # TEARDOWN
        result = jjx_down(charm_dir)
        assert result.returncode == 0, (
            f"jjx down exited with code {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    finally:
        # Safety net: tear down the model in case an assertion failed
        # before TEARDOWN completed.
        jjx_down(charm_dir)
