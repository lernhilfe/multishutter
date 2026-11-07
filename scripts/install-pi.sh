#!/usr/bin/env bash
# Run as the regular booth user on Raspberry Pi OS Lite 64 Bit.
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: bash scripts/install-pi.sh [--check-only]

Installs Python/gphoto2 through apt, runs the automated tests and a simulated
capture, and adds ~/.local/bin/multishutter. No physical camera is triggered.
Existing rig.local.toml is preserved. No passwords are stored.

--check-only  Check the OS, architecture and installed dependencies without changes.
EOF
}

check_only=false
case "${1:-}" in
    --help|-h) usage; exit 0 ;;
    --check-only) check_only=true ;;
    "") ;;
    *) usage >&2; exit 2 ;;
esac
if (( $# > 1 )); then usage >&2; exit 2; fi

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd -- "$project_dir"
if [[ "$(uname -s)" != Linux ]]; then
    echo 'Run this script ON the Raspberry Pi, not on your laptop.' >&2
    exit 1
fi
case "$(uname -m)" in
    aarch64|arm64) ;;
    *) echo 'Raspberry Pi OS 64 Bit (aarch64/arm64) is required.' >&2; exit 1 ;;
esac
if [[ ! -f /etc/debian_version ]] || ! command -v apt-get >/dev/null; then
    echo 'This installer requires a Debian-based Raspberry Pi OS with apt-get.' >&2
    exit 1
fi
if (( EUID == 0 )); then
    echo 'Run as the regular booth user; the installer uses sudo only for apt.' >&2
    exit 1
fi

if [[ "$check_only" == false ]]; then
    if [[ ! -w "$project_dir" ]]; then
        echo 'The booth user must own or have write access to the project directory.' >&2
        exit 1
    fi
    sudo -v
    sudo apt-get update
    sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        python3 python3-gphoto2 gphoto2
fi

/usr/bin/python3 - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit('Python >=3.11 required; use a current Raspberry Pi OS release.')
import gphoto2 as gp
print('Python:', sys.version.split()[0])
print('libgphoto2:', ', '.join(gp.gp_library_version(0)))
PY
gphoto2 --version

if [[ "$check_only" == true ]]; then
    echo 'Platform and dependency check passed. Nothing was changed.'
    exit 0
fi

/usr/bin/python3 -m unittest discover -s tests -v
/usr/bin/python3 - <<'PY'
import json
from pathlib import Path
import subprocess
import sys

result = subprocess.run([
    sys.executable, '-m', 'multishutter', 'capture',
    '--config', 'config/rig.simulated.toml',
    '--output', 'captures/install-check', '--takes', '3',
], check=True, text=True, stdout=subprocess.PIPE)
session = json.loads(result.stdout)
subprocess.run([
    sys.executable, '-m', 'multishutter', 'status', session['manifest'], '--verify',
], check=True)
assert session['simulated'] and session['status'] == 'complete'
print('Simulated installation check passed:', Path(session['manifest']).parent)
PY

if [[ ! -e config/rig.local.toml ]]; then
    cp config/rig.example.toml config/rig.local.toml
fi

launcher_dir="$HOME/.local/bin"
mkdir -p -- "$launcher_dir"
launcher="$launcher_dir/multishutter"
if [[ -e "$launcher" || -L "$launcher" ]]; then
    backup="$(mktemp "$launcher.backup.XXXXXXXX")"
    cp -L -- "$launcher" "$backup"
    echo "Previous launcher backed up to $backup"
fi
temporary_launcher="$(mktemp "$launcher_dir/.multishutter.XXXXXXXX")"
trap 'rm -f -- "$temporary_launcher"' EXIT
printf '#!/usr/bin/env bash\nset -euo pipefail\ncd -- %q\nexec /usr/bin/python3 -m multishutter "$@"\n' \
    "$project_dir" > "$temporary_launcher"
chmod 755 -- "$temporary_launcher"
mv -fT -- "$temporary_launcher" "$launcher"

echo "Installed launcher: $launcher"
echo 'Detecting cameras (no shutter release):'
if ! "$launcher" discover; then
    echo 'Software installed, but camera detection failed. Check USB mode and permissions.' >&2
    exit 1
fi
echo 'Installation and simulation verified. No physical camera was triggered.'
echo 'Next: map the detected models/ports to physical positions in config/rig.local.toml.'
echo 'Open a new login shell if ~/.local/bin is not yet on PATH.'
