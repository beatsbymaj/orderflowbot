#!/bin/bash
# FlowForge daily routine on the cloud server (docs/runbooks/aws-server.md). Paper only:
# the node itself refuses anything but IBKR paper.
#   morning: start the gateway and the helpers, download yesterday's ticks (the node seeds
#            its levels from them), check the plan, start the node.
#   evening: stop the node after the close.
# The node is NOT started when the kill switch is on or a halt is set: a human looks first
# and releases with riskctl (ADR-0013). A crashed node stays down until a human decides.
set -uo pipefail

cd "$(dirname "$0")/../.."
mkdir -p data/server-logs
exec >> "data/server-logs/$(date +%F).log" 2>&1
echo "=== $(date "+%F %T %Z") ${1:-}"

dc() { docker compose "$@"; }

case "${1:-}" in
  morning)
    dc up -d ib-gateway
    dc --profile paper up -d watchdog daily alerts dashboard
    status=$(dc --profile ops run --rm -T riskctl status)
    echo "$status"
    if ! grep -q '"kill_switch": "off"' <<<"$status" || ! grep -q '"halt": "none"' <<<"$status"; then
      echo "kill switch or halt is set: node NOT started (check, then riskctl release)"
      exit 0
    fi
    if [ -n "$(dc --profile paper ps -q node)" ]; then
      echo "node already running: left alone"
      exit 0
    fi
    dc --profile data run --rm -T ticks-download \
      python -m flowforge.data.alpaca_ticks --days 1 --out data/ticks \
      || echo "tick download failed: the node refuses entries on names without a prior day"
    dc --profile paper run --rm -T node --preflight || { echo "preflight refused: node NOT started"; exit 1; }
    dc --profile paper up -d node
    ;;
  evening)
    dc --profile paper stop node
    ;;
  *)
    echo "usage: $0 morning|evening" >&2
    exit 2
    ;;
esac
