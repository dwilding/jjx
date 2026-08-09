from . import (
    _virtual_grafana,
    _virtual_loki,
    _virtual_postgres,
    _virtual_prometheus,
    _virtual_traefik,
)

# Register all virtual charms. This must happen before any command module
# uses the registry. Importing here ensures registration on package import.
from . import _virtual_registry as _vr
from ._cli import run_hook_tool
from ._engine import _CONTAINER_BINARY
from ._version import __version__

_vr.register(
    _vr.VirtualCharmSpec(
        kind="postgresql",
        start=_virtual_postgres.start_postgres_wrapper,
        populate=_virtual_postgres.populate_relation,
        info_key="pg_info",
        endpoints={"database": {"interface": "postgresql_client", "role": "provides"}},
        teardown_priority=40,
    )
)
_vr.register(
    _vr.VirtualCharmSpec(
        kind="loki",
        start=_virtual_loki.start_loki,
        populate=_virtual_loki.populate_relation,
        info_key="loki_info",
        endpoints={"logging": {"interface": "loki_push_api", "role": "provides"}},
        display_name="Loki API",
        default_port=3100,
        teardown_priority=30,
    )
)
_vr.register(
    _vr.VirtualCharmSpec(
        kind="prometheus",
        start=_virtual_prometheus.start_prometheus,
        populate=_virtual_prometheus.populate_relation,
        info_key="prom_info",
        endpoints={"metrics-endpoint": {"interface": "prometheus_scrape", "role": "requires"}},
        display_name="Prometheus",
        default_port=9090,
        teardown_priority=20,
    )
)
_vr.register(
    _vr.VirtualCharmSpec(
        kind="grafana",
        start=_virtual_grafana.start_grafana,
        populate=_virtual_grafana.populate_relation,
        info_key="grafana_info",
        endpoints={"grafana-dashboard": {"interface": "grafana_dashboard", "role": "requires"}},
        display_name="Grafana",
        default_port=3000,
        teardown_priority=10,
    )
)
_vr.register(
    _vr.VirtualCharmSpec(
        kind="traefik",
        start=_virtual_traefik.start_traefik,
        populate=lambda model_state, relation, provider_app, info: None,
        info_key="traefik_info",
        teardown_priority=50,
    )
)


def container_runtime() -> str:
    return _CONTAINER_BINARY


__all__ = [
    "__version__",
    "container_runtime",
    "run_hook_tool",
]
