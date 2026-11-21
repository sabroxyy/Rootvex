#!/usr/bin/env bash
# Boot the c7200 image with Dynamips directly (no GNS3, no ghost IOS), once with
# the JIT compiler on and once with it off, and report which one boots.
#
#   bash lab/test_dynamips_direct.sh
#
# Takes about 2 minutes. Works in a temp folder, leaves nothing behind.

set -u
IMG="${1:-$HOME/GNS3/images/IOS/c7200-advipservicesk9-mz.152-4.S5.image}"
[ -f "$IMG" ] || { echo "Image not found: $IMG"; exit 1; }
DYN=$(command -v dynamips) || { echo "dynamips not installed"; exit 1; }
echo "dynamips: $DYN  ($($DYN 2>&1 | grep -m1 -oE 'version [^ ]+' || echo 'version ?'))"
echo "package:  $(dpkg -S "$DYN" 2>/dev/null | cut -d: -f1) $(dpkg-query -W -f='${Version}' dynamips 2>/dev/null)"
echo "image:    $IMG"
work=$(mktemp -d); trap 'rm -rf "$work"' EXIT

run() {  # $1 label, $2 seconds, rest = extra dynamips flags
  local label=$1 secs=$2; shift 2
  # an idle pipe keeps the console's input open without sending anything
  ( cd "$work" && sleep $((secs + 5)) | timeout "$secs" "$DYN" "$@" -P 7200 -t npe-400 -r 512 "$IMG" \
      >"$work/$label.txt" 2>&1 )
  local tlb ok
  tlb=$(grep -c 'TLB' "$work/$label.txt")
  ok=$(grep -cE 'Cisco IOS Software|Restricted Rights Legend|Press RETURN' "$work/$label.txt")
  if [ "$ok" -gt 0 ] && [ "$tlb" -eq 0 ]; then echo "BOOTS    $label"
  elif [ "$tlb" -gt 0 ]; then echo "CRASHES  $label  ($tlb TLB messages)"
  else echo "UNCLEAR  $label  (no IOS banner within ${secs}s; last lines below)"; tail -5 "$work/$label.txt" | sed 's/^/         /'
  fi
  rm -f "$work"/c7200_* "$work"/*_nvram "$work"/*_disk* "$work"/*_rom 2>/dev/null
}

echo
echo "Test 1: JIT ON  (normal, 40 s)"
run jit-on 40
echo "Test 2: JIT OFF (-j, slower, 120 s)"
run jit-off 120 -j
