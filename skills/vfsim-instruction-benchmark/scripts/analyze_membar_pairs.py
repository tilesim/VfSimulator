"""Qualify A5 archive-pair measurements and preserve exact operand-path evidence."""
from __future__ import annotations
import argparse,json,re
from collections import defaultdict
from pathlib import Path
import analyze_operator_pairs as ordinary_analysis
import membar_forwarding as probes
import vector_compute as compute
from core.params.database import ParamDB


def collect(roots):
    rows=[];runs=[]
    for root in roots:
        for work in sorted(root.glob('batch_*')):
            strict=work/'validation.strict.json';corrected=work/'validation.corrected.json'
            validation=strict if strict.exists() else corrected if corrected.exists() else work/'validation.json'
            if not validation.exists():continue
            records=json.loads(validation.read_text())
            if not records:continue
            # run_batch first writes generic validation; the pair-specific analyzer
            # replaces it after RV/form and operand-path checks have finished.
            if any((r['kind']=='ii' and any('form_key' not in e for e in r['events']))
                                        or (r['kind']=='fwd' and 'selected_forwarding' not in r) for r in records):continue
            dispatch={int(i):int(c) for c,i in re.findall(r'@(\d+)[^\n]*?\binstr_id (\d+)\b',(work/'core0.veccore0.rvec.simd.IDU.DISP.dump').read_text())}
            ready={}
            for line in (work/'core0.veccore0.rvec.ISU.dump').read_text().splitlines():
                m=re.search(r'\[(EXQ[01]|SHQ|LDQ)\] \[ISU_ISSUE\] instr_name (RV_\w+) instr_id (\d+).*?src_rdy (\d+)',line)
                if m and (m[1].startswith('EXQ') or m[2].startswith(('RV_VST','RV_PST','RV_VGATHER','RV_VSCATTER'))):ready[int(m[3])]=int(m[4])
            for row in records:
                row['run']=str(work.relative_to(root.parent))
                for event in row['events']:
                    event['dispatch_cycle']=dispatch.get(event['id'])
                    event['hardware_source_ready_cycle']=ready.get(event['id'])
                if row['kind']=='fwd' and row['api']!='membar_fwd':
                    old=row['source_role'];required=probes.roles(row['previous_key'],row['current_key'])
                    row['source_role']={'Vd':'data','Pg':'mask','Vm':'right','Vn':'left' if 'left' in required else 'vector'}.get(old,old)
                rows.append(row)
            runs.append(str(work))
    return rows,runs


def derive_forwarding(rows):
    samples=defaultdict(lambda:defaultdict(list))
    for row in rows:
        if row['kind']!='fwd' or not row['passed'] or not row['single_op_sample'] or not row.get('source_path_observable',True):continue
        events={e['id']:e for e in row['events']};row['qualified_forwarding']=[]
        for edge in row['selected_forwarding']:
            if not edge['other_sources_done_before_producer'] or not edge['dependency_reported_by_dispatch']:continue
            producer,consumer=events[edge['producer_id']],events[edge['consumer_id']]
            if row['previous_key'].split('.')[0] in probes.LOADS and producer['lsu_start'] is None:continue
            if producer['lsu_start'] is not None and producer['lsu_start']!=producer['cycle']:continue
            if consumer['lsu_start'] is not None and consumer['lsu_start']!=consumer['cycle']:continue
            if consumer.get('dispatch_cycle') is None or consumer['dispatch_cycle']+2>consumer['cycle']:continue
            ready=consumer.get('hardware_source_ready_cycle')
            if ready is not None and ready!=consumer['cycle']:continue
            observation=dict(edge,source_role=row['source_role'],run=row['run'],replica=row['replica'],producer_port=producer['port'],consumer_port=consumer['port'],producer_lsu_start=producer['lsu_start'],consumer_lsu_start=consumer['lsu_start'],consumer_hardware_ready=ready)
            samples[row['previous_key'],row['current_key']][row['source_role']].append(observation)
            row['qualified_forwarding'].append(observation)
    parameters={};summaries=[]
    for (previous,current),paths in sorted(samples.items()):
        required=set(probes.roles(previous,current))
        minima={role:min(v['gap'] for v in values) for role,values in paths.items()}
        counts={role:sum(v['gap']==minima[role] for v in values) for role,values in paths.items()}
        qualified=required.issubset(minima) and all(counts[r]>=2 for r in required)
        value=max(minima[r] for r in required) if qualified else None
        if qualified:parameters.setdefault(previous,{})[current]=value
        summaries.append(dict(producer=previous,consumer=current,required_roles=sorted(required),role_minima=minima,minimum_samples=counts,qualified=qualified,value=value,observations=dict(paths)))
    return parameters,summaries


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs',type=Path,nargs='+',required=True);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();rows,runs=collect(args.runs)
    parameters,ii_summaries=ordinary_analysis.derive(rows,ParamDB(base_dir=compute.ROOT))
    parameters['forwarding'],fwd_summaries=derive_forwarding(rows)
    result=dict(parameters=parameters,ii_summaries=ii_summaries,forwarding_summaries=fwd_summaries,records=rows,runs=runs)
    args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(batches=len(runs),cases=len(rows),numerical_pass=sum(r['passed'] and r['single_op_sample'] for r in rows),qualified_ii=sum(map(len,parameters['ii'].values())),qualified_forwarding=sum(map(len,parameters['forwarding'].values())),unqualified_ii=sum(not r['qualified'] for r in ii_summaries),unqualified_forwarding=sum(not r['qualified'] for r in fwd_summaries)),indent=2),flush=True)

if __name__=='__main__':main()
