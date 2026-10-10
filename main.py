#!/usr/bin/env python3
import argparse
import os
from api.common.schema import CanonicalVfInfo
from api.input_api import InputAPI
from core.simulator import CoreVfCostModel
def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='VF Simulator Main Entry')
    parser.add_argument('--trace', type=str, help='Path to the JSON trace file')
    parser.add_argument('--cce', type=str, help='Path to a CCE/DSL file. When provided, parse __VEC_SCOPE__ into CanonicalVfInfo.')
    parser.add_argument('--cce-kernel', type=str, default=None, help='Select a specific __VEC_SCOPE__ kernel name when the CCE file has multiple VF kernels.')
    parser.add_argument('--out_dir', type=str, default='results', help='Directory to save simulation logs')
    parser.add_argument('--theoretical-limit-vloop-only', action='store_true', help='Theoretical limit variant: keep top-level VLOOPv2 start timing but remove cross-iteration exposure gates')
    parser.add_argument('--theoretical-limit-vloop-only-legacy-forwarding-direct-issue', action='store_true', help='Theoretical limit variant: vloop-only + legacy-forwarding + single-queue direct issue (bypass SHQ->EXQ staging)')
    return parser

def resolve_trace_path(base_dir: str, trace_arg: str | None) -> str:
    trace_path = trace_arg or os.path.join(base_dir, 'tests', 'fixtures', 'canonical_vf_info', 'v1_valid_loop.json')
    if not os.path.isabs(trace_path):
        trace_path = os.path.join(base_dir, trace_path)
    return trace_path

def resolve_input_path(base_dir: str, path_arg: str) -> str:
    if os.path.isabs(path_arg):
        return path_arg
    return os.path.join(base_dir, path_arg)

def load_input_canonical_vf_info(base_dir: str, args: argparse.Namespace) -> tuple[CanonicalVfInfo, str]:
    if args.trace and args.cce:
        raise RuntimeError('Please provide only one input: --trace or --cce')
    if args.cce:
        cce_path = resolve_input_path(base_dir, args.cce)
        if not os.path.exists(cce_path):
            raise RuntimeError(f'CCE file not found: {cce_path}')
        print(f'[INFO] Loading CCE: {cce_path}')
        if args.cce_kernel:
            print(f'[INFO] CCE kernel = {args.cce_kernel}')
        return (InputAPI.load_cce(cce_path, kernel_name=args.cce_kernel), cce_path)
    trace_path = resolve_trace_path(base_dir, args.trace)
    if not os.path.exists(trace_path):
        raise RuntimeError(f'Trace file not found: {trace_path}')
    print(f'[INFO] Loading trace: {trace_path}')
    return (InputAPI.load_json(trace_path), trace_path)

def main():
    args = build_arg_parser().parse_args()
    base_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        vf_info, _ = load_input_canonical_vf_info(base_dir, args)
        model = CoreVfCostModel(
            base_dir=base_dir,
            out_dir=args.out_dir,
            theoretical_limit_vloop_only=args.theoretical_limit_vloop_only,
            theoretical_limit_direct_issue=args.theoretical_limit_vloop_only_legacy_forwarding_direct_issue,
        )
        result = model.run_vf_info(vf_info)
    except Exception as error:
        print(f"[ERROR] {error}")
        return 1
    print(f"[INFO] input contract = CanonicalVfInfo v{vf_info.schema_version}")
    print("Done. cycles_executed =", result["cycles_executed"])
    print("VF end cycle (with drain) =", result["vf_end_cycle"])
    print("Wrote logs to", result["results_dir"])
    print("Wrote Perfetto trace to", result["trace_path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
