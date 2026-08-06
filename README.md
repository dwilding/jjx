# jjx — Spin up K8s charms on Docker

jjx is an **experimental** test adapter for [Juju charms](https://canonical.com/juju/charms-architecture). It lets you run a charm's workload as a Docker container, with the charm's integration tests acting like a Compose file.

![jjx terminal demo](terminal.svg)

To be compatible with jjx, a K8s charm must use uv to manage its dependencies and have a dependency group called `integration`. jjx expects the integration tests to be located at `tests/integration` and use Jubilant with pytest-jubilant. For more detail about the expected structure, see [How to write integration tests for a charm](https://canonical.com/juju/docs/ops/latest/howto/write-integration-tests-for-a-charm/#write-your-tests).

**What's the point of jjx?**

*Speed* 💨

You can use jjx to quickly "run" a charm and play with its workload. No need to pack the charm or set up Juju.

**And what's the catch?**

The charm can't be too complex. If it requires storage, multiple units, or other charms, jjx might not work. The limitations are deliberately vague while jjx is a v0 tool.

## System requirements

- **[uv](https://docs.astral.sh/uv/getting-started/installation/)** — To install on Ubuntu, run `sudo snap install astral-uv --classic`.
- **[Docker](https://docs.docker.com/engine/install/)** — To install on Ubuntu, run `sudo snap install docker`.

By default, Docker commands require `sudo`, but jjx runs Docker commands as a regular user. To allow Docker commands, run `sudo usermod -aG docker $USER`, then log out and log in again.

## Demo

```sh
# Grab a small K8s charm that requires a PostgreSQL database.
git clone https://github.com/dwilding/fastapi-demo-operator.git
cd fastapi-demo-operator/

# Run the workload, using the integration tests marked 'smoke'.
uvx jjx -- -m smoke
```

We selected the smoke tests (the ones decorated `@pytest.mark.smoke`) because we want to minimize the footprint of what we're spinning up. The smoke tests deploy the charm and integrate it with a simulated PostgreSQL charm, producing containers for the workload and a database. The smoke tests don't deploy the observability apps the charm supports. Later in the demo we'll try jjx without `-m smoke`.

For now, open a second terminal and play with the workload:

```sh
curl http://172.17.0.2:8000/names  # returns {"names":{}}
curl -X POST -d 'name=elephant' http://172.17.0.2:8000/addname/
curl http://172.17.0.2:8000/names  # returns {"names":{"1":"elephant"}}
```

You might need to use a different IP address — check the output of `uvx jjx`.

For more detail about the workload, see [Study your application](https://canonical.com/juju/docs/ops/latest/tutorial/from-zero-to-hero-write-your-first-kubernetes-charm/study-your-application/) in the "zero to hero" K8s charm tutorial. Our charm is based on that tutorial.

> [!TIP]
> You can use [borescope](https://github.com/tonyandrewmeyer/borescope) to probe the workload:
>
> ```sh
> uvx borescope --socket .jjx/socket
> ```
>
> This gives you a prompt that feels like bash and has first-class support for Pebble commands. For example:
>
> <pre>
> <b>pebble:/#</b> ps
> <b>PID</b>  <b>TTY</b>  <b>TIME</b> <b>CMD</b>
>   1 ?      00:00:00 pebble
>  17 ?      00:00:00 uvicorn
> <b>pebble:/#</b> services
> <b>SERVICE</b>  <b>STARTUP</b>  <b>CURRENT</b>
> fastapi  enabled  active
> </pre>
>
> For more detail, see [Command reference](https://borescope.dev/docs/reference-commands.html) in the borescope docs.

Next, press Ctrl-C in your first terminal. This stops and removes all containers.

Then run the workload again, this time using the full suite of integration tests:

```sh
uvx jjx
```

In addition to deploying the charm and integrating it with PostgreSQL, the tests deploy [COS Lite](https://charmhub.io/cos-lite) and integrate the charm with Grafana, Prometheus, and Loki. The output of `uvx jjx` shows how to access these observability apps.

Finally, open Grafana and Prometheus in your browser and explore the available data. For ideas, see [Inspect the Grafana dashboard](https://canonical.com/juju/docs/ops/latest/tutorial/from-zero-to-hero-write-your-first-kubernetes-charm/observe-your-charm-with-cos-lite/#inspect-the-grafana-dashboard) and [Inspect metrics in Prometheus](https://canonical.com/juju/docs/ops/latest/tutorial/from-zero-to-hero-write-your-first-kubernetes-charm/observe-your-charm-with-cos-lite/#inspect-metrics-in-prometheus) in the "zero to hero" tutorial.

## Usage

### Run the workload

In the charm dir:

```text
uvx jjx
```

This runs the charm's integration tests and starts a Docker container for the workload — assuming the tests try to deploy a `.charm` file along with a workload image. See [How jjx works](#how-jjx-works).

The workload stays running until you press Ctrl-C.

The output shows the IP address of the workload. You can play with the workload by connecting to this address.

Alternatively, to play with the workload on localhost, specify a port mapping:

```text
uvx jjx -p <localhost-port>:<workload-port>
```

### Run the workload in the background

In the charm dir:

```text
uvx jjx -d
```

This does the same thing as `uvx jjx`, except the command exits and the workload stays running.

To stop the workload:

```text
uvx jjx down
```

### Set extra pytest options

jjx uses pytest to run the charm's integration tests. See [How jjx works](#how-jjx-works).

To set extra pytest options:

```text
uvx jjx -- <pytest-args>
```

For example:

```sh
# Enable verbose logging, to show more detail about each test.
uvx jjx -- -vv
```

To automatically include extra pytest options, use a `[tool.jjx]` table in `pyproject.toml`. For example:

```toml
[tool.jjx]
pytest-extra-args = ["-m", "smoke"]
```

## How jjx works

The `jjx` Python package provides a `juju` command that is partially compatible with the real `juju` command. Running `uvx jjx` in the charm dir is equivalent to:

```sh
touch placeholder.charm
uv run --group integration --with jjx pytest tests/integration --no-juju-teardown
rm placeholder.charm
```

When the integration tests try to deploy a `.charm` file along with a workload image, `juju` starts a container for the charm code. `juju` also starts a container for the workload and injects Pebble into the container. The charm code has access to the Pebble socket, as Ops expects.

Other Jubilant methods are handled by `juju` and routed to the charm code. For example, if a test calls `Juju.config()`, `juju` executes the charm code with its environment configured as a config-changed event. Ops recognizes the event and the charm code is able to apply the change using Pebble methods.

If the tests try to deploy the following charms/bundles, `juju` starts extra containers and simulates remote units.

| Supported charm/bundle | Extra containers |
| --- | --- |
| [postgresql-k8s](https://charmhub.io/postgresql-k8s) | [postgres](https://hub.docker.com/_/postgres) |
| [cos-lite](https://charmhub.io/cos-lite) | [grafana](https://hub.docker.com/r/grafana/grafana), [prometheus](https://hub.docker.com/r/prom/prometheus), [loki](https://hub.docker.com/r/grafana/loki) |

From the perspective of the charm and its tests, everything is real. The mocked parts are Juju, the cloud, and other charms.
