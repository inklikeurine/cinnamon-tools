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

# The package servers sometimes time out. A failed index download leaves
# stale package lists (and then "404 Not Found"), so refresh and install up
# to three times before giving up.
APT="apt-get -o Acquire::Retries=3 -qq"
install() {
  # cinnamon-common holds Cinnamon's own js/ tree and settings widgets; if a
  # release ever drops that package, the full cinnamon package has them too.
  $APT update -o APT::Update::Error-Mode=any \
    && { $APT install -y --no-install-recommends $PACKAGES cinnamon-common \
         || $APT install -y --no-install-recommends $PACKAGES cinnamon; }
}
for attempt in 1 2 3; do
  if install; then break; fi
  if [ "$attempt" = 3 ]; then echo "could not install the packages after 3 tries"; exit 1; fi
  echo "package install failed (try $attempt of 3); retrying in 20 seconds"
  sleep 20
done

# The system message bus is not running in a container; some tests need it.
mkdir -p /run/dbus
dbus-daemon --system --fork || echo "note: could not start the system message bus"

# The checked-out folders belong to a different user than the container's
# root, and git refuses to work in them without this.
git config --global --add safe.directory '*'

echo "cjs: $(cjs --version 2>&1)"
echo "python3: $(python3 --version 2>&1)"
