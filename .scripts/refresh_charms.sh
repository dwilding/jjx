#!/bin/bash
set -euo pipefail

rm -rf tests/functional/charms/*
rm -rf operator
git clone --depth 1 --single-branch https://github.com/canonical/operator.git
cp -r operator/examples/k8s-2-configurable tests/functional/charms
rm -rf operator
uv run --script .scripts/patch_charms.py
cd tests/functional/charms/k8s-2-configurable
UV_NO_CONFIG=1 tox -e format,lint,unit
