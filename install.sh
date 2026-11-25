#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID == 0 ]]; then echo 'Run ./install.sh as your desktop user; it will request sudo when needed.' >&2; exit 1; fi
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
# A half-installed older package with unsatisfied ABI dependencies can prevent
# APT from installing build prerequisites. Remove ONLY our own unconfigured
# package; dpkg --remove leaves the user's home configuration intact.
status=$(dpkg-query -W -f='${db:Status-Status}' fastcopy-mint 2>/dev/null || true)
case "$status" in
    unpacked|half-configured|half-installed)
        echo 'Removing the incomplete FastCopy Mint installation (saved jobs are retained).'
        sudo dpkg --remove fastcopy-mint ;;
esac
sudo apt update
sudo apt install -y build-essential cmake dpkg-dev python3 python3-tk libglib2.0-bin
# Never reuse a build directory from another distribution/toolchain.
local_build=$(mktemp -d "${TMPDIR:-/tmp}/fastcopy-mint-build.XXXXXX")
trap 'rm -rf -- "$local_build"' EXIT
FASTCOPY_BUILD_DIR="$local_build" ./build-deb.sh
version=0.2.8
if [[ ${FASTCOPY_STATIC:-0} == 1 ]]; then version+=+static1; fi
version+=+launcher1+tray1+command1+listing1+tray2+typeahead1
sudo apt install -y "./dist/fastcopy-mint_${version}_$(dpkg --print-architecture).deb"
fastcopy-mint-integrate
printf '\nInstalled. Open FastCopy from the application menu or run fastcopy.\n'
