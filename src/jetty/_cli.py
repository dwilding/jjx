"""Simulates the `juju` CLI."""

import sys

from . import _cmd_deploy


def main() -> None:
    if sys.argv[1] == "deploy":
        _cmd_deploy.deploy()
