#!/bin/bash
# 一键启动（Linux/macOS）——无 Docker 时直接跑 Python
set -e
cd "$(dirname "$0")"
export DASH_DATA_DIR="${DASH_DATA_DIR:-./data}"
export DASH_SCRIPTS_DIR="${DASH_SCRIPTS_DIR:-}"
mkdir -p "$DASH_DATA_DIR"
echo "Starting nexus-dashboard on port ${DASH_PORT:-28080}..."
exec python3 api.py
