# FastCopy Mint 0.2.8+static1+launcher1+tray1+command1+listing1+tray2+typeahead1: static-engine compatibility package

This static-engine release reserves space for the Source multi-selection instruction and moves New Folder into the destination picker header, retaining the previous destination semantics, log divider, filters, speed control and layout fixes. Source and DestDir now support case-insensitive type-ahead folder navigation. The original FastCopy 3.63 resource files are unchanged. The package version is 0.2.8+static1+launcher1+tray1+command1+listing1+tray2+typeahead1 so APT recognizes it as an upgrade from 0.2.8+static1+launcher1+tray1+command1+listing1+tray2. The sole GUI launcher is `fastcopy`; the old `fastcopy-mint` compatibility symlink has been removed. APT upgrades remove it automatically. The Debian package name is unchanged. The original static executable is unchanged. The listing no longer discards older output, and skipped preview entries are summarized. The updated minimize-to-tray helper uses the host GTK 3 library and prefers libxapp1 when present. The GUI also checks the X11 window-manager minimized state directly, retries with GTK if XApp registration fails, and uses FastCopy as the visible icon name and tooltip.

The CLI is statically linked using the distribution's C/C++ runtime libraries. The Tk GUI still uses the host's Python/Tk, and GSettings reads Cinnamon or MATE theme preferences when available (libglib2.0-bin is recommended rather than mandatory; the manual theme checkbox still works without it). No system libraries are replaced, downgraded or installed in private library paths. `readelf -lW` must show no INTERP or DYNAMIC program header, and `readelf -dW` must show no NEEDED entries. The supplied x86-64 build has a GNU ABI note of Linux 3.2.0 and an x86-64-baseline ISA requirement. This is not a guarantee of compatibility with every old kernel or operating system.

Install the prebuilt package on an AMD64 Mint desktop using APT (not bare dpkg), then launch as your normal user:

    sudo apt install ./fastcopy-mint_0.2.8+static1+launcher1+tray1+command1+listing1+tray2+typeahead1_amd64.deb
    fastcopy-mint-integrate
    fastcopy

No compiler is needed on the installation system. For a local build, install build-essential, cmake, dpkg-dev, python3, python3-tk and libglib2.0-bin, then use FASTCOPY_STATIC=1 ./build-deb.sh. The regular ./install.sh builds a dynamically linked executable against the target's own libraries; use that option if you want system-library updates to apply automatically. The static runtime must be rebuilt to receive runtime-library security fixes. Do not run copy operations as root. If switching to the older package version, remove the static package first; user settings remain in the home directory.

## Library notices and rebuilding/relinking

The supplied prebuilt executable uses unmodified Debian 13 development packages: glibc 2.41-12+deb13u3 and GCC 14.2.0-19 runtime libraries (libstdc++ and libgcc). See the Debian copyright notices and version manifest in the package's /usr/share/doc/fastcopy-mint directory and in static-relink/notices in the source bundle. The GNU C Library is predominantly LGPL-2.1-or-later; GCC runtime libraries carry their own copyright notices and GCC Runtime Library Exception. The original FastCopy and Linux application remain GPLv3, and xxHash retains its BSD license.

The exact library source package revisions are identified by the following Debian source package coordinates (source packages, including Debian patches, are available from Debian or its historical archive; these downloads have not been independently verified here):

- https://snapshot.debian.org/package/glibc/2.41-12%2Bdeb13u3/
- https://snapshot.debian.org/package/gcc-14/14.2.0-19/

With matching Debian source repositories enabled: `apt-get source glibc=2.41-12+deb13u3 gcc-14=14.2.0-19`. The library source archives are not embedded in this application bundle. If redistributing the static executable, obtain and provide the appropriate corresponding library source and fulfill the applicable license conditions.

The source bundle includes all application source, along with the application object file and its xxHash archive in static-relink. With a compatible compiler and chosen replacement C/C++ development libraries, use `c++ -static -pthread engine.cpp.o libupstream_xxhash.a -o fastcopy-mint-cli` in that directory, or rebuild the complete application from the included source with `cmake -S . -B build -DFASTCOPY_STATIC_ENGINE=ON -DCMAKE_BUILD_TYPE=Release` and `cmake --build build --parallel`. The static link does not prevent replacement or relinking of the libraries. The object file is built for x86-64 using GCC 14.2.0. A compatible toolchain or full source rebuild is needed.
