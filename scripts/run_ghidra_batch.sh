#!/usr/bin/env bash
# Batch Ghidra headless pseudocode extraction.
# Usage: run_ghidra_batch.sh <binary_dir_or_list> <out_dir> [parallel]
# Each binary -> <out_dir>/<binary_basename>.pseudo.jsonl
set -u
GHIDRA_HOME="${GHIDRA_HOME:-$HOME/pavlov-data/ghidra/ghidra_12.1.2_PUBLIC}"
HEADLESS="$GHIDRA_HOME/support/analyzeHeadless"
SCRIPTS="$(cd "$(dirname "$0")" && pwd)"
INPUT="$1"; OUTDIR="$2"; PAR="${3:-2}"
mkdir -p "$OUTDIR"
PROJBASE=$(mktemp -d /tmp/ghidra_proj.XXXXXX)

list_bins() {
  if [ -d "$INPUT" ]; then find "$INPUT" -type f; else cat "$INPUT"; fi
}

run_one() {
  bin="$1"
  base=$(basename "$bin")
  out="$OUTDIR/$base.pseudo.jsonl"
  log="$OUTDIR/$base.ghidra.log"
  if [ -s "$out" ]; then echo "skip $base (exists)"; return 0; fi
  proj="$PROJBASE/$base.$$"
  mkdir -p "$proj"
  timeout 1800 "$HEADLESS" "$proj" proj \
    -import "$bin" \
    -scriptPath "$SCRIPTS" \
    -postScript ExportPseudoC.java "$out" \
    -deleteProject -analysisTimeoutPerFile 900 -max-cpu 4 \
    > "$log" 2>&1
  rc=$?
  rm -rf "$proj"
  n=$( [ -f "$out" ] && wc -l < "$out" || echo 0 )
  echo "done $base rc=$rc funcs=$n"
}
export -f run_one
export HEADLESS SCRIPTS OUTDIR PROJBASE

list_bins | xargs -P "$PAR" -I{} bash -c 'run_one "$@"' _ {}
rm -rf "$PROJBASE"
