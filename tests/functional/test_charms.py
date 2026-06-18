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
    subprocess.run(
        command,
        cwd=charm_dir,
        check=True,
    )
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
