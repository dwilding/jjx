"""Simulates the `juju deploy` CLI command."""

import pathlib


def deploy() -> None:
    charm_code = pathlib.Path.cwd() / "src" / "charm.py"
    print(charm_code.resolve())
    assert charm_code.is_file()
