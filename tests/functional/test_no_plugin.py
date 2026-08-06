import pathlib
import subprocess

PACKAGE_DIR = pathlib.Path(__file__).parent.parent.parent


def test_pass_without_plugin(k8s_2_configurable):
    command = [
        "uv",
        "remove",
        "--group",
        "integration",
        "pytest-jubilant",
    ]
    subprocess.run(
        command,
        cwd=k8s_2_configurable,
        check=True,
    )
    (k8s_2_configurable / "tests" / "integration" / "conftest.py").write_text(
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
    test_charm = k8s_2_configurable / "tests" / "integration" / "test_charm.py"
    test_charm.write_text(test_charm.read_text().replace("@pytest.mark.juju_setup\n", ""))
    (k8s_2_configurable / "placeholder.charm").touch()
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
        cwd=k8s_2_configurable,
        check=True,
    )
    assert not (k8s_2_configurable / ".jjx").exists()
    assert (k8s_2_configurable / "placeholder.charm").exists()
    (k8s_2_configurable / "placeholder.charm").unlink()


def test_jjx_cli_needs_plugin(k8s_2_configurable):
    command = [
        "uvx",
        "--with-editable",
        PACKAGE_DIR,
        "jjx",
    ]
    result = subprocess.run(
        command,
        cwd=k8s_2_configurable,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "ERROR: pytest-jubilant is not in the 'integration' dependency group." in result.stderr
    assert not (k8s_2_configurable / ".jjx").exists()
    assert not (k8s_2_configurable / "placeholder.charm").exists()
