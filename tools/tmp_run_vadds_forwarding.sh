#!/usr/bin/env bash
set -euo pipefail
cd /mnt/d/VfSimulator
for dsl in VFtest/cce/benchmarks/forwarding/fwd_vadds_to_*.cce; do
  stem=$(basename "$dsl" .cce)
  echo "[CASE] $stem"
  bash ascend_runner/current/build_native_simexec.sh "$dsl" "$stem"
  bash ascend_runner/current/run_native_simexec.sh \
    "ascend_runner/build/${stem}_native_simexec/${stem}_simexec" \
    "ascend_runner/build/${stem}_native_simexec/${stem}_mix.o" \
    foo_add 1 1 64 || true
done