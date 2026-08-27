#!/usr/bin/env bash
# Grant this user access to /dev/uinput so the macro can inject input without
# running everything as root. This is the Linux equivalent of "run as
# administrator" on Windows — without it, injected clicks are silently dropped
# and the reel bar drifts to one side and gives up.
#
#   sudo bash LINUX/install-udev.sh
#
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo:  sudo bash LINUX/install-udev.sh"
  exit 1
fi

DIR="$(cd "$(dirname "$0")" && pwd)"
install -m 0644 "$DIR/99-uinput.rules" /etc/udev/rules.d/99-uinput.rules

modprobe uinput || true
# Load uinput automatically on future boots.
echo uinput > /etc/modules-load.d/uinput.conf

udevadm control --reload-rules
udevadm trigger

TARGET="${SUDO_USER:-$USER}"
usermod -aG input "$TARGET" || true

echo
echo "Granted '$TARGET' access to /dev/uinput (via the 'input' group)."
echo "Log out and back in (or reboot) for the group change to take effect,"
echo "then run:  python LINUX/run_linux.py"
