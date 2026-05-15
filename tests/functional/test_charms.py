import subprocess


def test_charm(package_dir, charm_dir):
    command = [
        "uv",
        "run",
        "--group",
        "integration",
        "--with-editable",
        package_dir,
        "pytest",
        "-v",
        "tests/integration",
    ]
    subprocess.run(
        command,
        cwd=charm_dir,
        check=True,
    )
