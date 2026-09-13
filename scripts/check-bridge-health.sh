#!/usr/bin/env bash
# Simple external health probe for finhubkh MT5 bridge.
# Usage: ./scripts/check-bridge-health.sh [base_url]
set -euo pipefail
BASE="${1:-http://bridge.finhubkh.com}"
echo "Probing $BASE/health ..."
body="$(curl -fsS --connect-timeout 8 --max-time 15 "$BASE/health")"
echo "$body" | python3 -m json.tool
ok="$(echo "$body" | python3 -c 'import sys,json; d=json.load(sys.stdin); raise SystemExit(0 if d.get("ok") and d.get("workers_alive") and d.get("redis",{}).get("ok") else 1)')"
echo "STATUS=healthy"
