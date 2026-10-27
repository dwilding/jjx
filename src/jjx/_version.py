__version__ = "0.6.1.dev0"

# The Juju version jjx reports to charms and to jubilant. The charm under test
# sees it as the JUJU_VERSION environment variable, and ``juju version`` and
# ``juju status`` return it to jubilant.
#
# The two consumers parse different formats:
#   - ops parses the JUJU_VERSION environment variable and accepts only
#     ``major.minor[.patch]`` (for example ``4.0.14``).
#   - jubilant parses the ``juju version`` JSON output and requires the
#     ``major.minor.patch-release-arch`` suffix (for example
#     ``4.0.14-jjx-amd64``).
#
# Pass JUJU_VERSION to charm code as the environment variable. Pass
# juju_version_string() to jubilant in the ``juju version`` and
# ``juju status`` JSON output.
JUJU_VERSION = "4.0.16"

# Pinned Pebble version. Must be a canonical/pebble release tag. Juju builds
# the Pebble binary it injects into containers from the github.com/canonical/pebble
# pin in its go.mod, so this must match that pin at the JUJU_VERSION release
# tag. The scheduled workflow (.github/workflows/latest-juju.yaml) keeps this
# up to date.
PEBBLE_VERSION = "v1.32.1"


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
