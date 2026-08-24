#!/bin/bash
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec python3 "$ROOT/demo/coding_demo.py"
