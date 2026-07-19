from ._version import __version__
from ._cli import run_hook_tool
from ._engine import _CONTAINER_BINARY


def container_runtime() -> str:
    """Return the container runtime binary to use (e.g. ``"docker"``).

    Currently always returns the value of ``_CONTAINER_BINARY`` (``"docker"``).
    The future podman PR will make this resolve the runtime via the
    ``JJX_RUNTIMES`` env var, trying each in order and returning the first that
    is on PATH and functional.
    """
    return _CONTAINER_BINARY


__all__ = [
    "__version__",
    "run_hook_tool",
    "container_runtime",
]
