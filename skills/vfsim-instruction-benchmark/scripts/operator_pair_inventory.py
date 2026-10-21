"""Inventory II matrices and register RAW pairs in complete VFtest operators."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from api.input_api import InputAPI
from api.common.instruction_catalog import DEFAULT_INSTRUCTION_CATALOG
from api.json.serialization import canonical_vf_info_to_dict
from core.execution.prepare import prepare_program
from core.params.database import ParamDB
from core.params.fallback import pair_key_candidates
from core.params.uarch import resolve_uarch


EXCLUDED = {"contracts", "ordinary_instructions", "fixtures", "cce_expected", "membar_timing"}
REGISTERS = {"Register", "PredicateRegister"}


def identity(value):
    return (value['definition_id'], tuple((item['loop_id'], item['iteration'])
                                         for item in value['iteration_path']))


def semantic_payload(value):
    if isinstance(value, dict):
        return {key: semantic_payload(item) for key, item in value.items()
                if key not in ('source', 'source_location')}
    if isinstance(value, list):
        return [semantic_payload(item) for item in value]
    return value


def forwarding_resolution(table, previous, current):
    if current in table.get(previous, {}):
        return 'exact', previous, current
    op, form = previous.split('.', 1)
    consumer_op, consumer_form = current.split('.', 1)
    spec = DEFAULT_INSTRUCTION_CATALOG.lookup(op)
    if spec is not None and spec.forwarding_opcode:
        kind, producer, consumer = forwarding_resolution(
            table, f'{spec.forwarding_opcode}.{form}', current)
        return ('catalog_inheritance' if kind != 'fallback' else kind), producer, consumer
    for producer, consumer, compatible in pair_key_candidates(op, form, consumer_op, consumer_form):
        if consumer in table.get(producer, {}):
            return ('compatible' if compatible else 'exact'), producer, consumer
    if '*' in table.get(previous, {}):
        return 'producer_wildcard', previous, '*'
    return 'fallback', previous, current


def inventory(root):
    db = ParamDB(base_dir=root)
    interval_table = json.loads((root / 'configs/InitiationInterval.json').read_text())['InitiationInterval']
    forwarding_table = json.loads((root / 'configs/forwarding.json').read_text())['forwarding']
    files, rejected, excluded = [], [], []
    intervals, forwarding = defaultdict(set), defaultdict(set)
    raw_paths = defaultdict(set)
    form_profiles = {}
    for path in sorted((root / 'VFtest').rglob('*')):
        if path.suffix not in ('.cce', '.json'):
            continue
        relative = str(path.relative_to(root))
        if any(part in EXCLUDED for part in path.relative_to(root / 'VFtest').parts):
            excluded.append(relative)
            continue
        try:
            vf = InputAPI.load_cce(path) if path.suffix == '.cce' else InputAPI.load_json(path)
            cfg = replace(resolve_uarch(db.base_uarch, vf.uarch),
                          canonical_dynamic_instruction_limit=0)
            program = prepare_program(vf, db, cfg)
            producers = {}
            compute_forms = {}
            observed_raw = Counter()
            opcode_counts = Counter()
            external_sources = Counter()
            for instruction in program.instructions:
                if instruction.type != 'inst':
                    continue
                profile = db.resolve_inst(instruction.op, instruction.form,
                                          instruction_class=instruction.op_class,
                                          memory_mode=instruction.attributes.get('catalog_mode'))
                key = f'{profile.op}.{profile.requested_form}'
                form_profiles[key] = profile
                opcode_counts[key] += 1
                if profile.op_class == 'COMPUTE':
                    compute_forms[key] = profile
                vector_index = 0
                for index, source in enumerate(instruction.src):
                    value = program.values[source]
                    if value['storage'] not in REGISTERS:
                        continue
                    role = 'predicate' if value['storage'] == 'PredicateRegister' else f'vector_{vector_index}'
                    if value['storage'] == 'Register':
                        vector_index += 1
                    previous = producers.get(identity(instruction.src_value_instances[index]))
                    if previous is None:
                        external_sources[key] += 1
                        continue
                    producer_key, result_index = previous
                    pair = (producer_key, key)
                    forwarding[pair].add(relative)
                    raw_paths[pair].add((role, result_index, value['dtype']))
                    observed_raw[pair] += 1
                for index, destination in enumerate(instruction.dst):
                    if program.values[destination]['storage'] in REGISTERS:
                        producers[identity(instruction.dst_value_instances[index])] = (key, index)
            per_file_ii = []
            for previous, a in compute_forms.items():
                for current, b in compute_forms.items():
                    if set(a.allowed_exu_ports) & set(b.allowed_exu_ports):
                        intervals[previous, current].add(relative)
                        per_file_ii.append((previous, current))
            payload = canonical_vf_info_to_dict(vf)
            files.append(dict(path=relative, sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                              semantic_sha256=hashlib.sha256(json.dumps(semantic_payload(payload), sort_keys=True).encode()).hexdigest(),
                              params=dict(vf.params), compute_forms=sorted(compute_forms),
                              dynamic_instructions=sum(opcode_counts.values()),
                              opcode_counts=dict(opcode_counts),
                              ii_pairs=len(per_file_ii),
                              ii_missing=sum(c not in interval_table.get(p, {}) for p, c in per_file_ii),
                              raw_pairs=[dict(producer=p, consumer=c, count=count)
                                         for (p, c), count in sorted(observed_raw.items())],
                              external_source_reads=dict(external_sources)))
        except Exception as error:
            rejected.append(dict(path=relative, error_type=type(error).__name__, error=str(error)))

    def interval_row(pair, paths):
        previous, current = pair
        explicit = interval_table.get(previous, {}).get(current)
        effective = db.get_ii_for_profiles(form_profiles[previous], form_profiles[current])
        return dict(previous=previous, current=current, explicit=explicit,
                    effective=effective, files=sorted(paths))

    def forwarding_row(pair, paths):
        previous, current = pair
        explicit = forwarding_table.get(previous, {}).get(current)
        effective = db.get_forwarding_for_profiles(form_profiles[previous], form_profiles[current])
        resolution, producer, consumer = forwarding_resolution(forwarding_table, previous, current)
        return dict(producer=previous, consumer=current, explicit=explicit,
                    effective=effective, resolution=resolution,
                    configured_producer=producer, configured_consumer=consumer, files=sorted(paths),
                    raw_paths=[dict(consumer_role=role, producer_result=result, dtype=dtype)
                               for role, result, dtype in sorted(raw_paths[pair])])

    ii_rows = [interval_row(pair, paths) for pair, paths in sorted(intervals.items())]
    fwd_rows = [forwarding_row(pair, paths) for pair, paths in sorted(forwarding.items())]
    return dict(scope='Complete operators and dependency probes in VFtest/cce, canonical and json; instruction contracts, fixtures and dedicated membar timing are excluded.',
                ii_definition='Full ordered compute opcode/form matrix within each input, restricted to pairs sharing an EXU port; union across files. Inventory expansion disables the instruction-count guard without modifying input or runtime defaults.',
                forwarding_definition='Register and predicate RAW pairs from expanded dynamic Canonical value identities, including loop-carried values.',
                summary=dict(files=len(files), semantic_programs=len({row['semantic_sha256'] for row in files}),
                             rejected_files=len(rejected), excluded_files=len(excluded),
                             compute_forms=len({key for row in files for key in row['compute_forms']}),
                             ii_pairs=len(ii_rows), ii_missing=sum(row['explicit'] is None for row in ii_rows),
                             forwarding_pairs=len(fwd_rows),
                             forwarding_missing_exact=sum(row['explicit'] is None for row in fwd_rows),
                             forwarding_missing=sum(row['resolution'] == 'fallback' for row in fwd_rows)),
                config_sha256={name:hashlib.sha256((root / 'configs' / name).read_bytes()).hexdigest()
                               for name in ['isa.json', 'forwarding.json', 'InitiationInterval.json', 'instruction_catalog.json']},
                files=files, rejected=rejected, excluded=excluded, ii=ii_rows, forwarding=fwd_rows,
                parameter_warnings=db.get_warnings())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = inventory(args.root.resolve())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result['summary'], indent=2))
    for row in result['rejected']:
        print(json.dumps(row))


if __name__ == '__main__':
    main()
