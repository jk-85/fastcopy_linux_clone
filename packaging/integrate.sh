#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID == 0 ]]; then echo 'Run this as your desktop user, not with sudo.' >&2; exit 1; fi
if [[ $# -gt 1 || ( $# == 1 && "$1" != --remove ) ]]; then echo 'Usage: fastcopy-mint-integrate [--remove]' >&2; exit 1; fi
script_content=$'#!/bin/sh\n# FastCopy Mint managed launcher\nexec fastcopy-mint-selection "$@"'
for dir in "${XDG_DATA_HOME:-$HOME/.local/share}/nemo/scripts" "${XDG_CONFIG_HOME:-$HOME/.config}/caja/scripts"; do
    path="$dir/Open in FastCopy Mint"
    if [[ -e "$path" || -L "$path" ]]; then
        if [[ -L "$path" ]] || ! cmp -s "$path" <(printf '%s\n' "$script_content"); then
            echo "Not touching existing custom launcher: $path" >&2; continue
        fi
    fi
    if [[ ${1:-} == --remove ]]; then rm -f -- "$path"; echo "Removed: $path"; continue; fi
    mkdir -p -- "$dir"
    printf '%s\n' "$script_content" > "$path"
    chmod 0755 "$path"
    echo "Installed: $path"
done
