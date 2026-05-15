import pathlib
import subprocess

PACKAGE_DIR = pathlib.Path(__file__).parent.parent.parent


def test_charm(charm_dir):
    command = [
        "uv",
        "run",
        "--group",
        "integration",
        "--with-editable",
        PACKAGE_DIR,
        "pytest",
        "-v",
        "tests/integration",
    ]
    subprocess.run(
        command,
        cwd=charm_dir,
        check=True,
    )
