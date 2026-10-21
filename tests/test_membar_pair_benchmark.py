"""Check that pair calibration observes real data flow and independent output bytes."""
import importlib
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS=Path(__file__).resolve().parents[1]/'skills/vfsim-instruction-benchmark/scripts'
sys.path.insert(0,str(SCRIPTS))
PROBES=importlib.import_module('membar_forwarding')
OPS=importlib.import_module('operator_pairs')
ANALYSIS=importlib.import_module('analyze_membar_pairs')
sys.path.remove(str(SCRIPTS))


def case(previous='VLDS.fp32',current='VSTS.fp32',role='data'):
    return dict(previous_key=previous,current_key=current,source_role=role,replica=0,kind='fwd')


class MembarPairBenchmarkTests(unittest.TestCase):
    def test_logarithm_chain_uses_positive_distinct_subtraction_results(self):
        _,data,golden,contracts=PROBES.make_batch([case('VSUB.fp32','VLN.fp32','vector')])
        left=PROBES.compute.decode(data[0:256],'fp32')
        right=PROBES.compute.decode(data[256:512],'fp32')
        result=PROBES.compute.decode(golden[1024:1280],'fp32')
        for a,b,y in zip(left,right,result):
            self.assertGreater(a,b)
            self.assertAlmostEqual(y,math.log(a-b),places=6)
        self.assertEqual(OPS.validate_regions(golden,golden,contracts[0]),[])

    def test_load_store_golden_rejects_unchanged_output(self):
        _,data,golden,contracts=PROBES.make_batch([case()])
        initial=data[len(golden):]
        self.assertNotEqual(initial[1024:1280],golden[1024:1280])
        self.assertTrue(OPS.validate_regions(initial,golden,contracts[0]))
        self.assertEqual(OPS.validate_regions(golden,golden,contracts[0]),[])
        corrupted=bytearray(golden);corrupted[1024:1280]=data[256:512]
        self.assertTrue(OPS.validate_regions(corrupted,golden,contracts[0]))

    def test_store_and_load_preserve_bits_even_within_float_tolerance(self):
        _,data,golden,contracts=PROBES.make_batch([case()])
        initial=data[len(golden):];corrupted=bytearray(golden)
        corrupted[1024]^=1
        self.assertEqual(OPS.validate_regions(corrupted,golden,contracts[0]),[])
        self.assertIn(1024,PROBES.copy_errors(corrupted,data,golden,contracts[0]))
        corrupted[0]^=1
        self.assertIn(0,PROBES.copy_errors(corrupted,data,golden,contracts[0]))

    def test_sparse_predicate_controls_float_lanes_and_output_guards(self):
        _,data,golden,contracts=PROBES.make_batch([case('PLT.b32','VADD.fp32','mask')])
        decode=PROBES.compute.decode
        left,right=decode(data[512:768],'fp32'),decode(data[768:1024],'fp32')
        actual=decode(golden[1024:1280],'fp32')
        for lane in range(64):
            self.assertAlmostEqual(actual[lane],left[lane]+right[lane] if lane<17 else 0,places=6)
        self.assertEqual(golden[3584:4096],data[len(golden)+3584:len(golden)+4096])
        corrupted=bytearray(golden);corrupted[3584]^=1
        self.assertIn(3584,OPS.validate_regions(corrupted,golden,contracts[0]))

    def test_predicate_to_bit_broadcast_is_observable_in_active_and_inactive_lanes(self):
        _,data,golden,contracts=PROBES.make_batch([case('PLDS.b8','VDUP.b32','mask')])
        expected=PROBES.compute.decode(golden[1024:1280],'uint32')
        self.assertEqual(set(expected),{0,1})
        wrong=bytearray(golden)
        wrong[1024:1280]=PROBES.compute.encode([1]*64,'uint32')
        self.assertTrue(OPS.validate_regions(wrong,golden,contracts[0]))
        wrong[1024:1280]=bytes(256)
        self.assertTrue(OPS.validate_regions(wrong,golden,contracts[0]))

    def test_scalar_broadcast_gate_and_both_selection_sources_are_observable(self):
        for role in ['left','right']:
            source,data,golden,contracts=PROBES.make_batch([case('VDUPS.fp32','VSEL.fp32',role)])
            self.assertIn('vdup(produced,scalar,late,MODE_ZEROING)',source)
            self.assertEqual(contracts[0]['consumer_mask_source'],'pa')
            actual=PROBES.compute.decode(golden[1024:1280],'fp32')
            other=PROBES.compute.decode(data[768:1024] if role=='left' else data[512:768],'fp32')
            mask=data[2048:2080]
            for lane,value in enumerate(actual):
                selected=PROBES.active(mask,lane,4)
                self.assertEqual(value,0.5 if selected==(role=='left') else other[lane])
            wrong=bytearray(golden);wrong[1024:1280]=PROBES.compute.encode(other,'fp32')
            self.assertTrue(OPS.validate_regions(wrong,golden,contracts[0]))

    def test_arithmetic_warmup_has_an_independent_closed_form_golden(self):
        sample=dict(case('PLT.b32','VABS.fp32','mask'),warm_operation='vadd',warm_length=4)
        _,data,golden,_=PROBES.make_batch([sample])
        a=PROBES.compute.decode(data[:256],'fp32');b=PROBES.compute.decode(data[256:512],'fp32')
        self.assertEqual(PROBES.compute.decode(golden[3072:3328],'fp32'),[x+4*y for x,y in zip(a,b)])

    def test_accumulator_paths_have_distinct_numeric_contracts(self):
        for role in ['old','vector']:
            _,data,golden,_=PROBES.make_batch([case('VMUL.fp32','VAXPY.fp32',role)])
            values=[PROBES.compute.decode(data[start:start+256],'fp32') for start in [0,256,512,768]]
            observed=PROBES.compute.decode(golden[1024:1280],'fp32')
            for a,b,left,right,result in zip(*values,observed):
                producer=PROBES.compute.quantize(a*b,'fp32')
                expected=producer+1.25*right if role=='old' else left+1.25*producer
                self.assertAlmostEqual(result,expected,places=5)

    def test_reduction_observes_only_defined_lanes_and_preserves_guards(self):
        source,data,golden,contracts=PROBES.make_batch([case('VADD.fp32','VCADD.fp32','vector')])
        initial=data[len(golden):]
        self.assertEqual(golden[1028:1280],initial[1028:1280])
        regions=[r for r in contracts[0]['output_regions'] if r['offset']==1024]
        self.assertEqual(regions,[dict(offset=1024,bytes=4,dtype='fp32')])
        self.assertIn('NORM_B32,one)',source)
        changed=bytearray(golden);changed[1028]^=1
        self.assertIn(1028,OPS.validate_regions(changed,golden,contracts[0]))
        source,data,golden,contracts=PROBES.make_batch([case('VCADD.fp32','VDIV.fp32','left')])
        self.assertIn('vdiv(consumed,(vector_f32)produced,cb,one,',source)
        self.assertEqual(PROBES.compute.decode(golden[1028:1280],'fp32'),[0.0]*63)

    def test_wide_predicate_probe_observes_each_result_before_reusing_registers(self):
        sample=dict(previous_key='PAND.b8',current_key='PLT.b32',kind='ii',replica=0,
                                                        order='paired',streams=6,wide_predicates=True,store_immediately=True)
        source,data,golden,contracts=OPS.make_batch([sample])
        self.assertEqual(contracts[0]['target_count'],12)
        self.assertEqual(golden[0:32],(1).to_bytes(32,'little'))
        self.assertEqual(golden[512:544],(0x111).to_bytes(32,'little'))
        self.assertLess(source.index('psts(r0_0,'),source.index('pand(r0_1,'))
        self.assertEqual(OPS.validate_regions(golden,golden,contracts[0]),[])
        changed=bytearray(golden);changed[512]^=1
        self.assertTrue(OPS.validate_regions(changed,golden,contracts[0]))

    def test_scalar_operands_load_before_the_vf_and_match_each_iota_base(self):
        sample=dict(previous_key='VCI.int32',current_key='PLT.b32',kind='ii',replica=0,
                                                        order='paired',streams=6,store_immediately=True,runtime_scalar_operands=True)
        source,data,golden,contracts=OPS.make_batch([sample])
        self.assertIn('vci(r0_0,n0_0,INC_ORDER);',source)
        self.assertIn('r1_0=plt_b32(n1_0,POST_UPDATE);',source)
        self.assertEqual(PROBES.compute.decode(data[3584+4*8:3588+4*8],'int32'),[12])
        self.assertEqual(PROBES.compute.decode(golden[4*256:5*256],'int32'),list(range(12,76)))
        self.assertEqual(golden[6*256:6*256+32],(0x1111).to_bytes(32,'little'))
        self.assertIn('wait_flag(PIPE_MTE2,PIPE_S',source)
        self.assertEqual(OPS.validate_regions(golden,golden,contracts[0]),[])

    def test_same_opcode_prefill_is_not_the_target_producer(self):
        row=dict(case(),output_offset=0,events=[dict(id=i) for i in [1,2,9,10]],forwarding_observations=[
            dict(producer_id=p,consumer_id=c,producer_op='RV_VLDS',consumer_op='RV_VSTS',dependency_reported_by_dispatch=True)
            for p,c in [(1,2),(9,10)]])
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory)
            for name in ['golden.bin','output.bin']:(work/name).write_bytes(bytes(4096))
            (work/'input.bin').write_bytes(bytes(8192))
            (work/'core0.veccore0.rvec.ISU.dump').write_text(
                '[SHQ] [ISU_RECV] instr_name RV_VLDS instr_id 9 PC 0 DST_VREGS:[v_idx=0,p_idx=7]\n'
                '[SHQ] [ISU_RECV] instr_name RV_VSTS instr_id 10 PC 4 SRC_VREGS:[v_idx=0,p_idx=7]\n')
            with patch.object(PROBES.compute,'analyze',return_value=[row]),patch.object(OPS,'validate_regions',return_value=[]):
                result=PROBES.analyze(work,contracts=[case()])[0]
            self.assertEqual([e['id'] for e in result['events']],[9,10])
            self.assertEqual(result['selected_forwarding'][0]['physical_dependencies'],[dict(kind='VREGS',physical_id=7)])

    def test_mask_data_readiness_and_lsu_queues_are_required(self):
        def observation(queued=False,ready=18):
            return dict(case(),api='membar_fwd',passed=True,single_op_sample=True,run='test',
                events=[dict(id=1,port=None,cycle=10,lsu_start=11 if queued else 10),
                                                dict(id=2,port=None,cycle=18,lsu_start=18,dispatch_cycle=12,hardware_source_ready_cycle=ready)],
                selected_forwarding=[dict(producer_id=1,consumer_id=2,gap=8,other_sources_done_before_producer=True,dependency_reported_by_dispatch=True)])
        self.assertEqual(ANALYSIS.derive_forwarding([observation(),observation()])[0],{'VLDS.fp32':{'VSTS.fp32':8}})
        self.assertEqual(ANALYSIS.derive_forwarding([observation(True),observation(True)])[0],{})
        self.assertEqual(ANALYSIS.derive_forwarding([observation(ready=17),observation(ready=17)])[0],{})
        hidden=dict(observation(),source_path_observable=False)
        self.assertEqual(ANALYSIS.derive_forwarding([hidden,hidden])[0],{})


if __name__=='__main__':unittest.main()
