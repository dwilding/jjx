"""Scan integration tests for dialed port literals and read the JUnit report."""

from __future__ import annotations

import ast
import re
import xml.etree.ElementTree as ET
from pathlib import Path

# A port literal: ":<port>" bounded by a URL start. The host part may be
# empty, which is how the constant parts of an f-string join up
# (f"http://{ip}:8000" joins to "http://:8000").
_PORT_RE = re.compile(r"https?://[^:/\s]*:(\d{2,5})(?!\d)")
# A testcase with any of these children did not pass.
_NOT_PASSED_TAGS = frozenset({"failure", "error", "skipped"})


def scan_test_ports(test_dir: Path) -> dict[str, set[int]]:
    """Map each test_* function under test_dir to the port literals it dials.

    A literal is a ``:<port>`` inside a string constant or the constant parts
    of an f-string, bounded by a URL start (``http://`` or ``https://``).
    Only literals inside the test function's own body count: ports computed
    through helpers or runtime values are invisible by design.
    """
    ports: dict[str, set[int]] = {}
    if not test_dir.is_dir():
        return ports
    for path in sorted(test_dir.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if not node.name.startswith("test_"):
                continue
            found = _function_ports(node)
            if found:
                ports[node.name] = found
    return ports


def _function_ports(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[int]:
    ports: set[int] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            ports.update(int(port) for port in _PORT_RE.findall(node.value))
        elif isinstance(node, ast.JoinedStr):
            literal = "".join(
                part.value
                for part in node.values
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
            )
            ports.update(int(port) for port in _PORT_RE.findall(literal))
    return ports


def passing_test_names(report_path: Path) -> list[str]:
    """Return the names of passing testcases from a JUnit report, in run order."""
    try:
        root = ET.parse(report_path).getroot()
    except (OSError, ET.ParseError):
        return []
    names = []
    for case in root.iter("testcase"):
        if any(child.tag in _NOT_PASSED_TAGS for child in case):
            continue
        names.append(case.get("name", ""))
    return names


def detected_ports(test_dir: Path | None, report_path: Path | None) -> set[int]:
    """Return the ports dialed by the last passing test that dialed any literal.

    An empty set means no passing test dialed a detectable port literal, or an
    input is missing (no report to read, no test directory to scan); the caller
    falls back to the IP-only workload line.
    """
    if test_dir is None or report_path is None:
        return set()
    scanned = scan_test_ports(test_dir)
    if not scanned:
        return set()
    winner: set[int] = set()
    for name in passing_test_names(report_path):
        base = name.split("[", 1)[0]
        if base in scanned:
            winner = scanned[base]
    return winner
