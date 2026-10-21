"""Qualify operator-specific mixed-form II observations by source, port and history."""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re

import operator_pairs as probes
import operator_forwarding as raw_probes
import vector_compute as compute
from core.params.database import ParamDB


def collect(roots):
    rows, runs = [], []
    for root in roots:
        for work in sorted(root.glob('batch_*')):
            if not (work / 'output.bin').exists():
                continue
            contracts = json.loads((work/'cases.json').read_text())
            records = (raw_probes if contracts[0]['kind'] == 'fwd' else probes).analyze(work)
            dispatch = {int(identity): int(cycle) for cycle, identity in re.findall(
                r'@(\d+)[^\n]*?\binstr_id (\d+)\b',
                (work / 'core0.veccore0.rvec.simd.IDU.DISP.dump').read_text())}
            source_ready = {int(identity):int(cycle) for identity,cycle in re.findall(
                r'\[EXQ[01]\] \[ISU_ISSUE\] instr_name RV_\w+ instr_id (\d+)[^\n]*?\bsrc_rdy (\d+)\b',
                (work/'core0.veccore0.rvec.ISU.dump').read_text())}
            for row in records:
                row['run'] = root.name + '/' + work.name
                for event in row['events']:
                    event['dispatch_cycle'] = dispatch.get(event['id'])
                    event['hardware_source_ready_cycle'] = source_ready.get(event['id'])
                rows.append(row)
            runs.append(dict(path=str(work), sha256={name:hashlib.sha256((work / name).read_bytes()).hexdigest()
                        for name in ['kernel.cce','cases.json','commands.json','input.bin','golden.bin','output.bin',
                                     'core0.veccore0.instr_popped_log.dump','core0.veccore0.instr_log.dump',
                                     'core0.veccore0.rvec.EXU.dump','core0.veccore0.rvec.ISU.dump','core0.veccore0.rvec.LSU.dump','core0.veccore0.rvec.simd.IDU.DISP.dump']}))
    return rows, runs


def derive(rows, db):
    samples = defaultdict(list)
    for row in rows:
        if row['kind'] != 'ii' or not row['passed'] or not row['single_op_sample']:
            continue
        events = {event['id']:event for event in row['events']}
        ids = set(events)
        if any(edge['producer_id'] in ids and edge['consumer_id'] in ids
               for edge in row['forwarding_observations']):
            row['independent_targets'] = False
            continue
        row['independent_targets'] = True
        row['qualified_ii'] = []
        for gap in row['independent_same_port_gaps']:
            previous, current = events[gap['previous_id']], events[gap['current_id']]
            a, b = previous['form_key'], current['form_key']
            if a == b:
                continue
            unit = gap['gap'] == 1
            pair_completion_conflict = False
            if not unit:
                ready=current.get('hardware_source_ready_cycle')
                if ready is not None:
                    if ready > previous['cycle'] + 1:continue
                elif not gap['sources_done_before_previous']:
                    continue
                if current.get('dispatch_cycle') is None or current['dispatch_cycle'] + 2 > previous['cycle']:
                    continue
                profile = db.resolve_inst(*b.split('.', 1))
                all_compute = row.get('all_compute_events', row['events'])
                candidate = previous['cycle'] + 1
                pair_completion_conflict = previous['done'] == candidate + profile.latency
                prior = [event for event in all_compute if event['port'] == current['port'] and event['cycle'] < candidate]
                if any(event['id'] != previous['id'] and event['done'] == candidate + profile.latency for event in prior):
                    continue
                if sum(event['done'] > candidate for event in prior) >= 7:
                    continue
                own_ii = db.get_ii_for_profiles(profile, profile)
                earlier = [event['cycle'] for event in events.values()
                           if event['port'] == current['port'] and event['form_key'] == b
                           and event['cycle'] < current['cycle']]
                if earlier and max(earlier) + own_ii > previous['cycle'] + 1:
                    continue
            observation = dict(gap, previous=a, current=b, previous_cycle=previous['cycle'],
                               current_cycle=current['cycle'], run=row['run'], replica=row['replica'],
                               order=row['order'], preceding_pair_completion_conflict=pair_completion_conflict, qualification='observed_unit_interval' if unit else 'hardware_ready_and_clear_pipeline' if current.get('hardware_source_ready_cycle') is not None else 'sources_and_self_ii_clear')
            samples[a, b].append(observation)
            row['qualified_ii'].append(observation)
    parameters, summaries = {}, []
    for (a, b), values in sorted(samples.items()):
        previous, current = [db.resolve_inst(*key.split('.', 1)) for key in (a, b)]
        ports = set(previous.allowed_exu_ports) & set(current.allowed_exu_ports)
        minima = {port:min(value['gap'] for value in values if value['port'] == port)
                  for port in ports if any(value['port'] == port for value in values)}
        counts = {port:sum(value['port'] == port and value['gap'] == gap for value in values)
                  for port, gap in minima.items()}
        qualified = (set(minima) == ports and len(set(minima.values())) == 1
                     and all(counts[port] >= 2 for port in ports))
        value = next(iter(minima.values())) if qualified else None
        if qualified:
            parameters.setdefault(a, {})[b] = value
        summaries.append(dict(previous=a, current=b, expected_ports=sorted(ports), port_minima=minima,
                              minimum_samples=counts, qualified=qualified, value=value, observations=values))
    return dict(isa={}, forwarding={}, ii=parameters), summaries



def derive_forwarding(rows):
    samples = defaultdict(lambda: defaultdict(list))
    for row in rows:
        if row['kind'] != 'fwd' or not row['passed'] or not row['single_op_sample']:
            continue
        events = {event['id']: event for event in row['events']}
        row['qualified_forwarding'] = []
        for edge in row['selected_forwarding']:
            if not edge['other_sources_done_before_producer'] or not edge['dependency_reported_by_dispatch']:
                continue
            producer, consumer = events[edge['producer_id']], events[edge['consumer_id']]
            # The model applies LOAD forwarding at LSU I1. Queuing before I1
            # cannot become part of the dependent compute's forwarding delay.
            if producer['lsu_start'] is not None and producer['lsu_start'] != producer['cycle']:
                continue
            if consumer.get('dispatch_cycle') is None or consumer['dispatch_cycle'] + 2 > producer['cycle'] + edge['gap']:
                continue
            observation = dict(edge, run=row['run'], replica=row['replica'],
                               producer_port=producer['port'], consumer_port=consumer['port'],
                               producer_lsu_start=producer['lsu_start'], consumer_lsu_start=consumer['lsu_start'])
            samples[row['previous_key'], row['current_key']][edge['role']].append(observation)
            row['qualified_forwarding'].append(observation)
    parameters, summaries = {}, []
    for (a, b), paths in sorted(samples.items()):
        required = set(raw_probes.roles(b))
        minima = {role: min(value['gap'] for value in values) for role, values in paths.items()}
        counts = {role: sum(value['gap'] == minima[role] for value in values) for role, values in paths.items()}
        qualified = required.issubset(minima) and all(counts[role] >= 2 for role in required)
        value = max(minima[role] for role in required) if qualified else None
        if qualified:
            parameters.setdefault(a, {})[b] = value
        summaries.append(dict(producer=a, consumer=b, required_roles=sorted(required),
                              role_minima=minima, minimum_samples=counts, qualified=qualified,
                              value=value, observations=dict(paths)))
    return parameters, summaries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs', type=Path, nargs='+', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    db = ParamDB(base_dir=compute.ROOT)
    rows, runs = collect(args.runs)
    parameters, summaries = derive(rows, db)
    parameters['forwarding'], forwarding_summaries = derive_forwarding(rows)
    result = dict(target=dict(cann='9.0.0-beta.1', soc='Ascend950PR_9599', compiler_target='dav-c310-vec'),
                  parameters=parameters, ii_summaries=summaries, forwarding_summaries=forwarding_summaries, records=rows, runs=runs)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(dict(cases=len(rows), passed=sum(row['passed'] and row['single_op_sample'] for row in rows),
                          qualified_forwarding=sum(len(values) for values in parameters['forwarding'].values()),
                          qualified_ii=sum(len(values) for values in parameters['ii'].values()),
                          unqualified=[dict(previous=row['previous'],current=row['current'],minima=row['port_minima'],counts=row['minimum_samples'])
                                       for row in summaries if not row['qualified']]),indent=2))


if __name__ == '__main__':
    main()
