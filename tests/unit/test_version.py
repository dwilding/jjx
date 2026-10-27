import re

from jjx import _version


def test_our_version():
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:\.dev\d+)?", _version.__version__)


def test_juju_version():
    assert re.fullmatch(
        rf"{re.escape(_version.JUJU_VERSION)}-jjx-[^-]+", _version.juju_version_string()
    )


def test_pebble_version():
    assert re.fullmatch(r"v\d+\.\d+\.\d+", _version.PEBBLE_VERSION)
