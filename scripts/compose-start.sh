#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
mode="${1:-up}"
if [[ "$mode" == demo-down ]]; then
    exec docker compose -f compose.demo.yaml down
fi
if grep -qi microsoft /proc/sys/kernel/osrelease && [[ -z "${MULTISHUTTER_BIND:-}" ]]; then
    export MULTISHUTTER_BIND="$(hostname -I | awk '{print $1}')"
fi
if [[ "$mode" == demo ]]; then
    if (( EUID == 0 )); then
        install -d -o 1000 -g 1000 demo-data demo-captures
    else
        mkdir -p demo-data demo-captures
    fi
    exec docker compose -f compose.demo.yaml up -d --build
fi
if (( EUID == 0 )); then
    install -d -o 1000 -g 1000 data captures
    if [[ ! -e /tmp/multishutter-1000.lock ]]; then
        install -o 1000 -g 1000 -m 600 /dev/null /tmp/multishutter-1000.lock
    fi
else
    mkdir -p data captures
    touch /tmp/multishutter-1000.lock
fi
if [[ "$mode" == down ]]; then
    exec docker compose down
fi
exec docker compose up -d --build
