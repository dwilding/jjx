import pathlib
import subprocess

PACKAGE_DIR = pathlib.Path(__file__).parent.parent.parent


def test_pass_without_plugin(k8s_1_minimal_patched):
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
    subprocess.run(
        command,
        cwd=charm_dir,
        check=True,
    )
    assert not (charm_dir / ".jjx").exists()


def test_jjx_cli_needs_plugin(k8s_1_minimal_patched):
    charm_dir = k8s_1_minimal_patched
    command = [
        "uvx",
        "--with-editable",
        PACKAGE_DIR,
        "jjx",
    ]
    result = subprocess.run(
        command,
        cwd=charm_dir,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "pytest-jubilant is not in the 'integration' dependency group" in result.stderr
