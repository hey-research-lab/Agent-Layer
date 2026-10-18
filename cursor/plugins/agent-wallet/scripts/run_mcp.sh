#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
PLUGIN_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd -P)
OPENCLAW_HOME=${OPENCLAW_HOME:-"$HOME/.openclaw"}
PACKAGE_ROOT=${AGENT_WALLET_PACKAGE_ROOT:-${OPENCLAW_AGENT_WALLET_PACKAGE_ROOT:-"$OPENCLAW_HOME/agent-wallet-runtime/current/agent-wallet"}}

if [ -n "${AGENT_WALLET_PYTHON:-}" ]; then
  PYTHON_BIN=$AGENT_WALLET_PYTHON
elif [ -n "${OPENCLAW_AGENT_WALLET_PYTHON:-}" ]; then
  PYTHON_BIN=$OPENCLAW_AGENT_WALLET_PYTHON
elif [ -x "$PACKAGE_ROOT/.venv/bin/python" ]; then
  PYTHON_BIN=$PACKAGE_ROOT/.venv/bin/python
elif [ -x "$PACKAGE_ROOT/.runtime-venv/bin/python" ]; then
  PYTHON_BIN=$PACKAGE_ROOT/.runtime-venv/bin/python
else
  PYTHON_BIN=python3
fi

if [ ! -f "$PLUGIN_ROOT/server.py" ]; then
  printf '{"error":"AgentLayer Cursor bridge server.py is missing.","fix":"Reinstall the Cursor plugin from the AgentLayer marketplace."}\n' >&2
  exit 1
fi

if ! "$PYTHON_BIN" -c 'import sys, ast; ast.parse(open(sys.argv[1], encoding="utf-8").read())' "$PLUGIN_ROOT/server.py" 2>/dev/null; then
  printf '{"error":"AgentLayer Cursor bridge server.py failed to parse.","fix":"Reinstall the Cursor plugin and wallet runtime."}\n' >&2
  exit 1
fi

: "${AGENT_WALLET_HOST:=cursor}"
export AGENT_WALLET_HOST
exec "$PYTHON_BIN" "$PLUGIN_ROOT/server.py"
