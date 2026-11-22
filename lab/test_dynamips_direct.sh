#!/usr/bin/env bash
# Boot the c7200 image with Dynamips directly (no GNS3, no ghost IOS), once with
# the JIT compiler on and once with it off, and report which one boots.
#
#   bash lab/test_dynamips_direct.sh
#
# Takes at most ~3 minutes, usually seconds: each run stops as soon as IOS boots or
# crashes. Works in a temp folder (capped at 200 MB) and leaves nothing behind.

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
  local out="$work/$label.txt"
  # An idle pipe keeps the console's input open; head caps the output at 2 MB.
  ( cd "$work" && sleep $((secs + 5)) | "$DYN" "$@" -P 7200 -t npe-400 -r 512 "$IMG" 2>&1 \
      | head -c 2000000 >"$out" ) &
  local job=$! t=0
  # Stop as soon as we know the answer: a crash loop writes millions of lines per minute.
  while [ $t -lt "$secs" ]; do
    sleep 1; t=$((t + 1))
    grep -qE 'TLB|Press RETURN|Cisco IOS Software' "$out" 2>/dev/null && { sleep 2; break; }
    du -sm "$work" 2>/dev/null | awk '{exit !($1 > 200)}' && break   # safety: >200 MB in temp
  done
  # match only the dynamips process (this script's own command line also holds the image path)
  pkill -f "^$DYN .*-P 7200 " 2>/dev/null; sleep 1; pkill -9 -f "^$DYN .*-P 7200 " 2>/dev/null
  wait $job 2>/dev/null
  local tlb ok
  tlb=$(grep -c 'TLB' "$out")
  ok=$(grep -cE 'Cisco IOS Software|Restricted Rights Legend|Press RETURN' "$out")
  if [ "$ok" -gt 0 ] && [ "$tlb" -eq 0 ]; then echo "BOOTS    $label  (after ${t}s)"
  elif [ "$tlb" -gt 0 ]; then echo "CRASHES  $label  (after ${t}s)"; grep -m2 -E 'TLB|PC =' "$out" | sed 's/^/         /'
  else echo "UNCLEAR  $label  (no IOS banner within ${secs}s; last lines below)"; tail -5 "$out" | sed 's/^/         /'
  fi
  rm -rf "${work:?}"/* 2>/dev/null
}

free_mb=$(df -Pm "${TMPDIR:-/tmp}" | awk 'NR==2 {print $4}')
[ "$free_mb" -lt 1024 ] && { echo "Only ${free_mb} MB free in ${TMPDIR:-/tmp}; free some disk space first."; exit 1; }

echo
echo "Test 1: JIT ON  (normal, 40 s)"
run jit-on 40
echo "Test 2: JIT OFF (-j, slower, 120 s)"
run jit-off 120 -j
