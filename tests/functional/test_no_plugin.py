import pathlib
import subprocess

PACKAGE_DIR = pathlib.Path(__file__).parent.parent.parent


def test_pytest_after_plugin_removal(k8s_1_minimal_patched):
    charm_dir = k8s_1_minimal_patched
    command = [
        "uv",
        "remove",
        "--group",
        "integration",
        "pytest-jubilant",
    ]
    subprocess.run(
        command,
        cwd=charm_dir,
        check=True,
    )
    (charm_dir / "tests" / "integration" / "conftest.py").write_text(
        "import pathlib\n"
        "\n"
        "import pytest\n"
        "import jubilant\n"
        "\n"
        "\n"
        '@pytest.fixture(scope="module")\n'
        "def charm():\n"
        '    return pathlib.Path("placeholder.charm").resolve()\n'
        "\n"
        "\n"
        '@pytest.fixture(scope="module")\n'
        "def juju():\n"
        "    with jubilant.temp_model() as juju:\n"
        "        yield juju\n"
    )
    test_charm = charm_dir / "tests" / "integration" / "test_charm.py"
    test_charm.write_text(test_charm.read_text().replace("@pytest.mark.juju_setup\n", ""))
    (charm_dir / "placeholder.charm").touch()
    command = [
        "uv",
        "run",
        "--group",
        "integration",
        "--with-editable",
        PACKAGE_DIR,
        "pytest",
        "tests/integration",
    ]
    result = subprocess.run(
        command,
        cwd=charm_dir,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"pytest exited with code {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert not (charm_dir / ".jjx").exists()
