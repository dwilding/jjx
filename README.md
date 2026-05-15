jjx is a **very experimental** test adapter for Kubernetes charms.

jjx provides a `juju` command that can "deploy" unpacked charms:

- The charm source runs locally, inside a lightweight sandbox environment
- The workload runs as a Docker container

There's no cloud or controller, but from the charm's perspective everything is real. Pebble is really managing the container and the expected hook tools are available.

jjx's `juju` command is intended to be a drop-in replacement for charm integration tests. The `juju` CLI has just enough Juju-compatible functionality that Jubilant thinks it's talking to the real thing.

### Why on earth?

Fast intergration tests. _Laughably fast_ integration tests.

### And the catch?

No relations. No scaling. No actions (for now). No secrets (for now). I think you get my point.

### Requirements

- uv
- [bubblewrap](https://github.com/containers/bubblewrap) (for the lightweight sandbox)
- Docker

### Demo

```sh
# Grab a simple Kubernetes charm
git clone https://github.com/canonical/operator.git
cd operator/examples/httpbin-demo

# "Pack" the charm (otherwise the integration tests will complain)
touch placeholder.charm

# Go!
uv run --group integration --with jjx pytest -v tests/integration
```

Output:

```
...
collected 2 items

tests/integration/test_charm.py::test_deploy PASSED                         [ 50%]
tests/integration/test_charm.py::test_block_on_invalid_config PASSED        [100%]

---------------------------------- jubilant --------------------------------------
Models were torn down. To keep models available for subsequent test runs or manual
debugging, pass the following:
--no-juju-teardown
=============================== 2 passed in 8.41s ================================
```
