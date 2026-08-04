import pathlib
import subprocess

PACKAGE_DIR = pathlib.Path(__file__).parent.parent.parent


def test_charm(charm_dir):
    command = [
        "uvx",
        "--with-editable",
        PACKAGE_DIR,
        "jjx",
        "-d",
    ]
    result = subprocess.run(
        command,
        cwd=charm_dir,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"jjx exited with code {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    if charm_dir.name == "k8s-5-observe":
        assert "Grafana" in result.stdout
        assert "Prometheus" in result.stdout
        assert "Loki API" in result.stdout
    command = [
        "uvx",
        "--with-editable",
        PACKAGE_DIR,
        "jjx",
        "down",
    ]
    subprocess.run(
        command,
        cwd=charm_dir,
        check=True,
    )
