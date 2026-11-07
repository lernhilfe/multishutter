#!/usr/bin/env bash
# Executed as root inside WSL only, not on the Windows host.
set -euo pipefail
linux_user="${1:?WSL username is required}"
if [[ ! "$linux_user" =~ ^[a-z_][a-z0-9_-]*$ ]]; then
    echo 'Invalid Linux username.' >&2
    exit 2
fi
if (( EUID != 0 )) || ! grep -qi 'microsoft.*WSL2' /proc/sys/kernel/osrelease; then
    echo 'Run this provisioning stage as root inside WSL2 via Setup-Windows.cmd.' >&2
    exit 1
fi
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    python3 python3-venv python3-gphoto2 gphoto2 usbutils udev
/usr/bin/python3 -c 'import sys, gphoto2; assert sys.version_info >= (3, 11), "Python >=3.11 required"'
groupadd -f plugdev
if ! id "$linux_user" >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash "$linux_user"
fi
if [[ "$(id -u "$linux_user")" == 0 ]]; then
    echo 'The capture user must not be root.' >&2
    exit 1
fi
usermod -aG plugdev "$linux_user"
linux_home="$(getent passwd "$linux_user" | cut -d: -f6)"
venv="$linux_home/.local/share/multishutter/venv"
project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runuser -u "$linux_user" -- /usr/bin/python3 -m venv "$venv"
runuser -u "$linux_user" -- "$venv/bin/python" -m pip install --only-binary=:all: -r "$project_root/requirements-wsl.txt"
# A WSL command-line user may not have a desktop uaccess session. Restrict access
# to the plugdev group, and only to explicitly forwarded Sony USB devices.
cat > /etc/udev/rules.d/99-multishutter-sony.rules <<'RULE'
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="054c", MODE="0660", GROUP="plugdev"
RULE
if udevadm control --reload-rules; then
    udevadm trigger --subsystem-match=usb
    udevadm settle --timeout=10
else
    echo 'udev is not running. Restart WSL before forwarding cameras so the USB access rule is applied.' >&2
fi
echo "Linux dependencies ready; capture runs as $linux_user without a password or app service."
