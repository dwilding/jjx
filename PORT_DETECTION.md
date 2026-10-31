# Port detection

This is an implementation plan for a fresh checkout of `main` (`7e3b708`), where jjx runs workloads with Docker and `-p` is the only port mechanism. Every anchor below was verified against that branch, and every behavioral claim about real charms and real images was verified live in a working session.

## What we're building

After an integration run, jjx tells the user where to connect **without making them guess the port**:

```text
Workload running at 172.17.0.5:8000
```

The line matches today's `-p` line: `Workload running at <ip>:<port>`. The port comes from the **last passing integration test that dialed a port literal**, in run order. jjx reads nothing but the tests: which ports they dial, and which of them passed. It performs no check of its own against the workload.

Currency is the tests' job, and that is the design. If a test reconfigures the port and no later passing test dials the new port, the line goes stale — a bug in the tests that jjx has now exposed for the charm author to fix. jjx does not cross-check the workload to paper over that bug. Reporting the tests' verdict, including a stale one, is the feature.

When no passing test dialed any port literal, the output falls back to exactly today's line: the container IP alone. The fallback carries no extra words, so a user who never reads release notes sees nothing new until it helps them.

## The two inputs

| Input | When jjx reads it | What it answers |
|---|---|---|
| Port literals in the integration tests | After the run, before printing `Workload running at ...` | Which port each test dialed |
| The JUnit report | Same point; pytest's built-in plugin writes it during the run | Which tests passed, in run order |

Both are read in one new post-run phase in `jjx_cli()`, in the block that builds `workload_line` today. Nothing runs before the pytest session, so nothing can delay or break it.

## Decisions, in dependency order

### 1. The scanner: a new module, AST-based, function-scoped

New file `src/jjx/_port_scan.py`. It walks the AST of every `*.py` under the integration test directory (the first positional target in the final `pytest_args`, which defaults to `tests/integration` and which `jjx_pytest_args()` assembles from defaults, `pytest-extra-args`, and CLI args). For each `test_*` function it collects port literals:

- Port literals inside string constants, for example `urlopen("http://172.0.0.5:8000/names")`.
- Port literals inside the constant parts of f-strings, for example `f"http://{unit_ip}:8000"` — the shape the fastapi tests use.
- A literal means the regex `:(\d{2,5})` bounded by a URL start (`http://` or `https://`).

The return type is `dict[str, set[int]]`: test name to dialed ports.

The deliberate limits, each with a verdict:

- **No dataflow analysis.** The fastapi test assigns `api_base = f"http://{unit_ip}:8000"` and then dials `f"{api_base}/names"`; the literal is in one function, so function scoping catches it. If a test computes its port through a helper or from `juju.config()`, the scanner misses it, the test cannot back any port, and the output degrades to the IP-only line. I accept that: missed detections are silent and harmless.
- **Runtime-assembled URLs are invisible.** `test_loki_data` builds its URL from the runtime result of `show-proxied-endpoints`, so no literal exists to scan. The COS virtual endpoint lines (printed by `_cos_endpoint_lines()`) already cover that charm class. I accept the miss.
- **Tests that dial nothing scan to nothing.** The `k8s-2-configurable` fixture has exactly one `http` occurrence in its integration tests (the Apache license header), so a jjx run against it degrades to the IP-only line. That is the negative test case for the feature.

### 2. The JUnit report as evidence, deduplicated with the user's own

jjx appends `--junit-xml=<charm_root>/.jjx/junit.xml` to the pytest command it already builds in `jjx_cli()` (call site of `jjx_pytest_args()`). Three rules:

- **Absolute path, and jjx creates `.jjx/` first.** jjx should not rely on the plugin to create parent directories.
- **Deduplicate.** If the final pytest args already carry `--junit-xml`/`--junitxml` (any of the two-token or `=` forms, from defaults, `pytest-extra-args`, or post-`--` CLI args), jjx uses that path — resolved against the charm root, the same way pytest resolves it — and does not append its own.
- **Stdlib parsing.** `xml.etree.ElementTree` reads the file. A `<testcase>` passes if it has no `<failure>`, `<error>`, or `<skipped>` child. Parametrized names (`test_adds[case]`) match the scanner key by splitting at `[`. The plugin writes `<testcase>` elements in execution order, so walking the document gives the run order — which is what makes "the last passing dialing test" well-defined.

Why a report file instead of a pytest plugin: the plugin would need `jjx` importable in the charm's integration environment, which is true in only two of the three launch modes (charm venv and local checkout) and false in tool installs, where the `juju` shim is a standalone script. The report file needs no import and no new dependency: the plugin ships inside pytest, and `ElementTree` is standard library.

### 3. The output: last passing dial, in the existing block

In the post-run block of `jjx_cli()`, where `workload_line` is built from `_running_workload_container()` today:

- Walk the JUnit report in run order. The winner is the last passing `<testcase>` that the scanner gave ports to. For each port it dialed, print one line: the container IP and the port. The line carries no test name. pytest already prints which tests ran and passed (`-v` is in the default args), so whoever wants the test behind the port finds it in the session output.
- No passing test dialed any literal: print exactly today's line, `Workload running at <ip>`, byte for byte.
The position, and its consequences:

- **`-p` disables detection entirely.** `-p` is the user supplying the port themselves. Detection exists for the case where they have no port to supply, so under `-p` jjx skips it all: the scan, the `--junit-xml` argument, the report read. The line stays today's `127.0.0.1:<external>` unchanged. A detected port printed alongside would name a container-IP endpoint that `-p` never published, and filtering those would be a cross-check — testing the user's mapping against the tests' dials — which this design does not do.

## Sequence

Each step leaves the tree green under `just lint` and `just unit`:

1. `src/jjx/_port_scan.py` — scanner module.
2. The JUnit argument in `jjx_pytest_args()`'s call path, plus the parser.
3. The cross-reference and output changes in the `jjx_cli()` post-run block.
4. The golden test extensions below, then the documentation updates.

## Tests

House rules apply: no unit tests (the code is not stable enough yet), functional tests only, matching `tests/functional/STYLE.md` strictly. Extend `tests/functional/test_golden_charms.py`:

- **Positive:** `test_fastapi_demo` already clones `dwilding/fastapi-demo-operator`. After the run, assert the workload line names `:8000` and nothing else. The port comes from `test_app_add_name`: it dials `f"http://{unit_ip}:8000"`, and it is the last passing test in the suite that dials any literal — the eight other tests dial nothing scannable.
- **Negative:** the fixture-charm test that runs `jjx` against `k8s-2-configurable` (today `test_charmcraft_profile`) asserts the line stays `Workload running at <ip>` with no port suffix.
- Leave `test_httpbin_demo` alone. It asserts model and container plumbing; coupling it to port behavior buys nothing.

## Documentation

- `README.md`: the paragraph that says "The output shows the workload's IP address" gains the new behavior: when a passing test dialed a port literal, jjx prints that port; otherwise the IP alone, as before. The docs state plainly that jjx reports the tests' verdict and does not verify against the workload itself.
- `DESIGN.md`: a new decision entry under "## the `jjx` command" covering the scanner's scope and its deliberate misses, JUnit as the evidence channel (and why not a plugin), and the last-passing-dial semantics — jjx reads only what the tests did, exposes stale verdicts as test bugs rather than cross-checking them away.

## Out of scope

- Any cross-check against the workload (live plan reads, port probing, socket scans). Currency is the tests' job.
- Transitive port detection (helpers, config-driven ports), runtime-assembled URLs, and virtual-charm endpoints beyond the existing COS lines.
- The `-p` output line and the publish mapping. `-p` disables detection; it does not change anything else about `-p`.
