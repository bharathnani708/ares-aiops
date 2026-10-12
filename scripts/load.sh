#!/usr/bin/env bash
# Generate steady traffic so metrics, logs and spans have something to show.
#   ./scripts/load.sh [requests-per-second] (needs ./scripts/ui.sh running)
set -uo pipefail
RPS=${1:-5}
echo "Sending ~${RPS} req/s to http://localhost:8081/api/telemetry (Ctrl+C to stop)"
while true; do
  for _ in $(seq "$RPS"); do curl -s -o /dev/null -m 10 http://localhost:8081/api/telemetry & done
  sleep 1
done
