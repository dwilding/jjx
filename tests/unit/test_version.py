import re

from jjx import _version


def test_our_version():
    assert _version.__version__


def test_juju_version():
    assert re.fullmatch(
        rf"{re.escape(_version.JUJU_VERSION)}-jjx-[^-]+", _version.juju_version_string()
    )
