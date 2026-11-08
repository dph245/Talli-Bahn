#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ziel="${script_dir}/latest_$(date +%F).zip"
temp_datei="$(mktemp "${ziel}.part.XXXXXX")"
trap 'rm -f -- "$temp_datei"' EXIT

echo "Lade GTFS-Daten nach ${ziel} ..."
curl --fail --location --retry 3 \
    --output "$temp_datei" \
    "https://download.gtfs.de/germany/free/latest.zip"
mv -f -- "$temp_datei" "$ziel"
echo "Gespeichert: ${ziel}"
