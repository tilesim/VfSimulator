"""Compiler motion preserves feedback, loop scope and dynamic value lifetimes."""
from collections import Counter
from dataclasses import replace
import tempfile
from pathlib import Path
import unittest

from api.input_api import InputAPI
from api.common.schema import CanonicalInstruction, CanonicalLoop
from api.common.validator import validate_canonical_vf_info
from api.json.serialization import canonical_vf_info_to_dict
from api.json.adapter import CanonicalJsonVfInfoAdapter
from core.execution.optimization import hoist_loop_invariants
from core.simulator import CoreVfCostModel
from tests.core_support import ROOT, expanded


def parse(body, *, declarations='', params='__ubuf__ float *a,__ubuf__ float *out,uint32_t n'):
    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / 'licm.cce'
        source.write_text(f'void vf({params}){{{declarations}__VEC_SCOPE__{{'
                          'vector_f32 x,y,z;vector_bool p,q,all;'
                          'all=pset_b32(PAT_ALL);' + body + '}}')
        return InputAPI.load_cce(source)


def counts(vf):
    return Counter(i.op for i in expanded(vf) if i.type == 'inst')


class LoopInvariantTests(unittest.TestCase):
    def test_checked_in_fixture_separates_fixed_and_feedback_masks(self):
        vf = InputAPI.load_cce(ROOT/'VFtest/cce/contracts/loop_invariants.cce')
        _, stats = self.optimize(vf)
        self.assertEqual([m['opcode'] for m in stats['moves']], ['PLT','VDUPS'])
        self.assertEqual(counts(vf)['PLT'], 5)
        self.assertEqual(counts(vf)['VDUPS'], 1)

    def optimize(self, vf):
        optimized, stats = hoist_loop_invariants(vf)
        self.assertTrue(validate_canonical_vf_info(optimized).ok,
                        validate_canonical_vf_info(optimized).diagnostics)
        return optimized, stats

    def test_reset_and_feedback_scalar_counts_have_distinct_dependencies(self):
        cases = [
            ('uint32_t k=37;', '', 1),
            ('uint32_t k=n+5;', '', 1),
            ('uint32_t k=13+i*7;', '', 4),
            ('', 'uint32_t k=161;', 4),
        ]
        for reset, declaration, expected in cases:
            with self.subTest(reset=reset, declaration=declaration):
                vf = parse(declaration + 'for(int i=0;i<4;++i){' + reset +
                           'p=plt_b32(k,POST_UPDATE);vlds(x,a,i*64,NORM);'
                           'vsel(y,x,x,p);vsts(y,out,i*64,NORM_B32,all);}')
                optimized, stats = self.optimize(vf)
                self.assertEqual(counts(vf)['PLT'], expected)
                self.assertEqual(sum(m['opcode']=='PLT' for m in stats['moves']), expected == 1)
                self.assertEqual(hoist_loop_invariants(optimized)[1]['hoisted_instruction_count'], 0)

    def test_predicate_chain_and_broadcast_share_one_dynamic_definition(self):
        vf = parse('for(int i=0;i<4;++i){uint32_t k=n;'
                   'p=plt_b32(k,POST_UPDATE);pnot(q,p,all);'
                   'vdup(y,2.5f,q,MODE_ZEROING);vsts(y,out,i*64,NORM_B32,all);}'
                   'vsts(y,out,256,NORM_B32,all);')
        optimized, stats = self.optimize(vf)
        self.assertEqual([m['opcode'] for m in stats['moves']], ['PLT','PNOT','VDUPS'])
        self.assertEqual(counts(vf)['VDUPS'], 1)
        instructions = [i for i in expanded(vf) if i.type == 'inst']
        broadcast = next(i for i in instructions if i.op == 'VDUPS')
        stores = [i for i in instructions if i.op == 'VSTS']
        self.assertEqual(len(stores), 5)
        self.assertTrue(all(store.src[0] == broadcast.dst[0] for store in stores))
        with tempfile.TemporaryDirectory() as folder:
            result = CoreVfCostModel(base_dir=ROOT, out_dir=folder).run_vf_info(vf)
        self.assertEqual(result['compiler_optimization_stats'], stats)
        self.assertNotEqual(vf.context, optimized.context)

    def test_nested_motion_stops_at_the_loop_owning_the_scalar(self):
        vf = parse('for(int j=0;j<2;++j){uint32_t outer=n+j*7;'
                   'for(int i=0;i<4;++i){uint32_t k=outer;p=plt_b32(k,POST_UPDATE);'
                   'vlds(x,a,j*256+i*64,NORM);vsel(y,x,x,p);'
                   'vsts(y,out,j*256+i*64,NORM_B32,all);}}')
        _, stats = self.optimize(vf)
        move = next(m for m in stats['moves'] if m['opcode'] == 'PLT')
        self.assertEqual((move['from_loop'], move['to_loop']), ('loop.1','loop.0'))
        self.assertEqual(counts(vf)['PLT'], 2)

    def test_constant_chain_crosses_two_loops_with_unique_move_count(self):
        vf = parse('for(int j=0;j<2;++j){for(int i=0;i<4;++i){'
                   'p=pset_b32(PAT_VL32);vdup(y,2.5f,p,MODE_ZEROING);'
                   'vsts(y,out,j*256+i*64,NORM_B32,all);}}'
                   'vsts(y,out,512,NORM_B32,all);')
        _, stats = self.optimize(vf)
        self.assertEqual(stats['hoisted_instruction_count'], 2)
        self.assertEqual(len(stats['moves']), 4)
        self.assertEqual(counts(vf)['VDUPS'], 1)

    def test_direct_induction_expression_is_not_invariant(self):
        vf = parse('for(int i=0;i<4;++i){p=pltm_b32((uint16_t)i,n);'
                   'vdup(y,(uint32_t)i+3u,p,MODE_ZEROING);vsts(y,out,i*64,NORM_B32,all);}')
        _, stats = self.optimize(vf)
        self.assertEqual(stats['hoisted_instruction_count'], 0)
        self.assertEqual(counts(vf)['PLTM'], 4)
        self.assertEqual(counts(vf)['VDUPS'], 4)

    def test_explicit_scalar_update_is_loop_carried(self):
        vf = parse('uint32_t k=n;for(int i=0;i<4;++i){'
                   'p=pltm_b32(0,k);vdup(y,2.5f,p,MODE_ZEROING);'
                   'vsts(y,out,i*64,NORM_B32,all);k+=7;}')
        _, stats = self.optimize(vf)
        self.assertEqual(stats['hoisted_instruction_count'], 0)
        self.assertEqual(counts(vf)['PLTM'], 4)

    def test_unmodeled_increment_expressions_are_rejected(self):
        for statement in ('uint32_t k=n++;', 'uint32_t k=(n+=1);',
                          'uint32_t k=(n<<=1);', 'vdup(y,++n,all,MODE_ZEROING);'):
            with self.subTest(statement=statement), self.assertRaises(ValueError):
                parse('for(int i=0;i<4;++i){'+statement+'}')

    def test_vector_feedback_and_merging_destination_stay_inside(self):
        for instruction in ('vadds(x,x,1.0f,all);', 'vdup(x,1.0f,p,MODE_MERGING);'):
            with self.subTest(instruction=instruction):
                vf = parse('p=pset_b32(PAT_VL32);vlds(x,a,0,NORM);'
                           'for(int i=0;i<4;++i){'+instruction+'vsts(x,out,i*64,NORM_B32,all);}')
                _, stats = self.optimize(vf)
                self.assertEqual(stats['hoisted_instruction_count'], 0)

    def test_zero_trip_unknown_effects_and_volatile_are_retained(self):
        cases = [
            ('for(int i=0;i<0;++i){p=pset_b32(PAT_VL32);}', ''),
            ('for(int i=0;i<4;++i){p=movp_b32();}', ''),
            ('for(int i=0;i<4;++i){p=pltm_b32(0,k);}', 'volatile uint32_t k=37;'),
            ('for(int i=0;i<4;++i){uint32_t k=get_block_idx();p=plt_b32(k,POST_UPDATE);}', ''),
            ('for(int i=0;i<4;++i){vlds(x,a,0,NORM);vsts(x,out,0,NORM_B32,all);}', ''),
        ]
        for body, declarations in cases:
            with self.subTest(body=body):
                vf = parse(body, declarations=declarations)
                _, stats = self.optimize(vf)
                self.assertEqual(stats['hoisted_instruction_count'], 0)

    def test_scalar_shadowing_preserves_outer_feedback(self):
        vf = parse('uint32_t k=n;for(int j=0;j<2;++j){p=plt_b32(k,POST_UPDATE);'
                   'for(int i=0;i<4;++i){uint32_t k=37;q=plt_b32(k,POST_UPDATE);'
                   'vlds(x,a,j*256+i*64,NORM);vsel(y,x,x,q);'
                   'vsts(y,out,j*256+i*64,NORM_B32,p);}}')
        _, stats = self.optimize(vf)
        outer = next(n for n in vf.context if isinstance(n, CanonicalLoop))
        outer_plt = next(n for n in outer.body if isinstance(n, CanonicalInstruction))
        self.assertNotIn(outer_plt.instruction_id, {m['instruction_id'] for m in stats['moves']})
        self.assertEqual(counts(vf)['PLT'], 3)  # invariant inner + two outer updates

    def test_json_metadata_roundtrip_and_invalid_references(self):
        vf = parse('for(int i=0;i<4;++i){uint32_t k=n+i;p=plt_b32(k,POST_UPDATE);}')
        payload = canonical_vf_info_to_dict(vf)
        self.assertEqual(CanonicalJsonVfInfoAdapter.from_payload(payload), vf)
        identity = next(k for k,v in vf.values.items() if v.scalar_loop_dependencies)
        values = dict(vf.values)
        values[identity] = replace(values[identity], scalar_dependencies=('missing',))
        invalid = replace(vf, values=values)
        self.assertIn('invalid_scalar_dependency', [d.code for d in validate_canonical_vf_info(invalid).diagnostics])

    def test_compiled_input_marker_keeps_the_original_dynamic_stream(self):
        vf = parse('for(int i=0;i<4;++i){p=pset_b32(PAT_VL32);vsts(x,out,i*64,NORM_B32,p);}')
        raw = replace(vf, source={**vf.source,'compiler_optimized': True})
        self.assertEqual(counts(vf)['PSET_B32'], 2)
        self.assertEqual(counts(raw)['PSET_B32'], 5)
        optimized, stats = self.optimize(raw)
        self.assertIs(optimized, raw)
        self.assertFalse(stats['enabled'])
