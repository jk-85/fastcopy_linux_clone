#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
root=$PWD
version=0.2.8
static=OFF
case "${FASTCOPY_STATIC:-0}" in
    0) ;;
    1) static=ON; version+=+static1 ;;
    *) echo 'FASTCOPY_STATIC must be 0 or 1' >&2; exit 2 ;;
esac
version+=+launcher1+tray1+command1+listing1+tray2+typeahead1+menu1
build="${FASTCOPY_BUILD_DIR:-$root/build}"
mkdir -p "$build"
arch=$(dpkg --print-architecture)
cmake -S "$root" -B "$build" -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr -DFASTCOPY_STATIC_ENGINE="$static"
cmake --build "$build" --parallel "${FASTCOPY_BUILD_JOBS:-$(nproc)}"
ctest --test-dir "$build" --output-on-failure
stage=$(mktemp -d)
trap 'rm -rf -- "$stage"' EXIT
DESTDIR="$stage/pkg" cmake --install "$build"
mkdir -p "$stage/pkg/DEBIAN" "$stage/debian"
cat > "$stage/debian/control" <<CONTROL
Source: fastcopy-mint
Section: utils
Priority: optional
Maintainer: FastCopy Mint local build <local@localhost>
Standards-Version: 4.6.2

Package: fastcopy-mint
Architecture: any
Description: Linux file transfer manager
CONTROL
# Use the actual ELF binary, not assumptions, to determine ABI dependencies.
engine="$stage/pkg/usr/bin/fastcopy-mint-cli"
if [[ $static == ON ]]; then
    headers=$(LC_ALL=C readelf -lW "$engine")
    dynamic=$(LC_ALL=C readelf -dW "$engine")
    if grep -Eq '[[:space:]](INTERP|DYNAMIC)[[:space:]]' <<< "$headers" || grep -q '(NEEDED)' <<< "$dynamic"; then
        echo 'Refusing to package: static engine still has dynamic dependencies' >&2
        exit 1
    fi
    shared_deps=''
    docdir="$stage/pkg/usr/share/doc/fastcopy-mint"
    major=$(g++ -dumpversion | cut -d. -f1)
    for pkg in libc6-dev "libgcc-${major}-dev"; do
        cp -L "/usr/share/doc/$pkg/copyright" "$docdir/$pkg-copyright.txt"
    done
    for license in LGPL-2.1 LGPL-3 GPL-3; do
        cp -L "/usr/share/common-licenses/$license" "$docdir/$license.txt"
    done
    dpkg-query -W -f='${Package} ${Version} (source: ${source:Package} ${source:Version})\n' libc6-dev "libgcc-${major}-dev" "libstdc++-${major}-dev" > "$docdir/static-library-versions.txt"
    cp "$root/STATIC-BUILD.md" "$docdir/STATIC-BUILD.md"
else
    shared_deps=$(cd "$stage" && dpkg-shlibdeps -l"/usr/lib/$(dpkg-architecture -qDEB_HOST_MULTIARCH)" -O -e"$engine" | sed -n 's/^shlibs:Depends=//p')
    [[ -n "$shared_deps" ]] || { echo 'Unable to calculate runtime dependencies' >&2; exit 1; }
    shared_deps+=', '
fi
cat > "$stage/pkg/DEBIAN/control" <<CONTROL
Package: fastcopy-mint
Version: $version
Section: utils
Priority: optional
Architecture: $arch
Maintainer: FastCopy Mint local build <local@localhost>
Depends: ${shared_deps}python3 (>= 3.9), python3-tk, libgtk-3-0 | libgtk-3-0t64
Recommends: libglib2.0-bin, libxapp1
Description: Native Linux file transfer manager with FastCopy 3.63 resource-layout interface
 C++ transfer engine, xxHash64 verification, original-resource Tk desktop interface,
 preview, pause and cancel, minimize-to-tray, and optional Nemo/Caja integration.
CONTROL
mkdir -p "$root/dist"
output="$root/dist/fastcopy-mint_${version}_${arch}.deb"
dpkg-deb --root-owner-group --build "$stage/pkg" "$output"
echo "Package: $output"
