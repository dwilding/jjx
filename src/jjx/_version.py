__version__ = "0.5.1.dev0"

# Pinned Pebble version. Must be a canonical/pebble release tag.
PEBBLE_VERSION = "v1.32.1"

# The Juju version jjx reports to charms and to jubilant. This is the version
# the charm under test sees via JUJU_VERSION and that ``juju version`` returns
# to jubilant. Bump this when targeting a newer Juju series.
#
# The two consumers require different string formats:
#   - jubilant's Version parser (``juju version --format json``) needs
#     ``major.minor.patch-release-arch`` (e.g. ``4.0.14-jjx-amd64``); a bare
#     ``4.0.14`` is rejected.
#   - ops's JujuVersion parser (the JUJU_VERSION env var) accepts only
#     ``major.minor[.patch]`` (e.g. ``4.0.14``); the ``-release-arch`` suffix
#     is rejected.
# Use JUJU_VERSION for the env var and juju_version_string() for the
# ``juju version``/``juju status`` JSON output.
JUJU_VERSION = "4.0.14"


def _host_arch() -> str:
    """Map the host machine name to the Juju architecture string."""
    import os

    return {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }.get(os.uname().machine, os.uname().machine)


def juju_version_string() -> str:
    """Return the full ``major.minor.patch-release-arch`` version for jubilant.

    Jubilant's ``Version._from_dict`` (parsing ``juju version --format json``)
    requires the ``-release-arch`` suffix; a bare ``major.minor.patch`` is
    rejected. This is distinct from :data:`JUJU_VERSION`, which is the
    ``major.minor.patch`` form passed to charm code via the ``JUJU_VERSION``
    environment variable (ops's ``JujuVersion`` parser rejects the suffix).
    """
    # "jjx" as the release makes it clear this is not a real Juju build.
    return f"{JUJU_VERSION}-jjx-{_host_arch()}"
