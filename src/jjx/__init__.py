from ._version import __version__
from ._cli import run_hook_tool
from ._engine import _CONTAINER_BINARY


def container_runtime() -> str:
    return _CONTAINER_BINARY


__all__ = [
    "__version__",
    "container_runtime",
    "run_hook_tool",
]
