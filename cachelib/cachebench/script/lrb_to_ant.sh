#!/bin/bash
# Convert an LRB-format trace ("timestamp id size ...", space-separated) into
# the annotated format cachebench's replay generator expects:
#   next op_time key size
# The first line is a dummy header (the generator always discards it).
# next = index of the key's next request, 4294967295 if never requested again.
# Usage: lrb_to_ant.sh <input.tr> <output.tr.ant>
set -euo pipefail

if [ $# -ne 2 ]; then
  echo "usage: $0 <input.tr> <output.tr.ant>" >&2
  exit 1
fi

IN="$1"
OUT="$2"
N=$(wc -l < "$IN")

{
  echo "next op_time key size"
  tac "$IN" | awk -v N="$N" '{
      i = N - NR
      nxt = ($2 in last) ? last[$2] : 4294967295
      last[$2] = i
      print nxt, $1, $2, $3 }' | tac
} > "$OUT"
