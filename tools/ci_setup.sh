#!/bin/sh
# Installs what the validator and the headless tests need, inside the
# Linux Mint 22.3 container the pull request checks run in (checks.yml):
# Cinnamon 6.6's script engine (cjs) and shared JavaScript, the settings
# definitions and Python libraries applets use, a throwaway X display for
# tests that open a window, and a message bus for the D-Bus tests.
#
#   sh cinnamon-tools/tools/ci_setup.sh
#
# For CI only: it installs packages as root. On a Cinnamon desktop all of
# this is already there.
set -eu
export DEBIAN_FRONTEND=noninteractive

PACKAGES="cjs cinnamon-desktop-data
  python3 python3-gi gir1.2-glib-2.0 gir1.2-gtk-3.0 gir1.2-gdkpixbuf-2.0 gir1.2-xapp-1.0 gir1.2-gsound-1.0
  dbus dbus-x11 xvfb xauth git ca-certificates"

apt-get update -qq
# cinnamon-common holds Cinnamon's own js/ tree and settings widgets; if a
# release ever drops that package, the full cinnamon package has them too.
apt-get install -y -qq --no-install-recommends $PACKAGES cinnamon-common \
  || apt-get install -y -qq --no-install-recommends $PACKAGES cinnamon

# The system message bus is not running in a container; some tests need it.
mkdir -p /run/dbus
dbus-daemon --system --fork || echo "note: could not start the system message bus"

# The checked-out folders belong to a different user than the container's
# root, and git refuses to work in them without this.
git config --global --add safe.directory '*'

echo "cjs: $(cjs --version 2>&1)"
echo "python3: $(python3 --version 2>&1)"
