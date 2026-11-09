"""Check mixed dtype goldens and reject confounded operator timing samples."""

import importlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/vfsim-instruction-benchmark/scripts"
sys.path.insert(0, str(SCRIPTS))
PROBES = importlib.import_module("operator_pairs")
ANALYSIS = importlib.import_module("analyze_operator_pairs")
INVENTORY = importlib.import_module("operator_pair_inventory")
PARAMETERS = importlib.import_module("analyze_vector_compute")
sys.path.remove(str(SCRIPTS))


class Database:
    def resolve_inst(self, op, form):
        return SimpleNamespace(allowed_exu_ports=(0, 1), latency=6)

    def get_ii_for_profiles(self, previous, current):
        return 4


def interval(gap=1):
    events, gaps = [], []
    for port in (0, 1):
        previous, current = 10 * port + 1, 10 * port + 2
        events += [dict(id=previous, port=port, form_key="VADD.fp32", cycle=10, done=16),
                   dict(id=current, port=port, form_key="VEXP.fp32", cycle=10 + gap,
                        done=16 + gap, dispatch_cycle=5)]
        gaps.append(dict(previous_id=previous, current_id=current, port=port,
                         gap=gap, sources_done_before_previous=True))
    return dict(kind="ii", passed=True, single_op_sample=True, events=events,
                forwarding_observations=[], independent_same_port_gaps=gaps,
                replica=0, run="test", order="paired")


def forwarding(role="Vn", gap=2):
    return dict(kind="fwd", passed=True, single_op_sample=True,
                previous_key="VDUPS.fp32", current_key="VADD.fp32", run="test", replica=0,
                events=[dict(id=1, port=0, cycle=10, lsu_start=None),
                        dict(id=2, port=1, cycle=10 + gap, lsu_start=None, dispatch_cycle=5)],
                selected_forwarding=[dict(producer_id=1, consumer_id=2, gap=gap,
                                          role=role, other_sources_done_before_producer=True,
                                          dependency_reported_by_dispatch=True)])


class OperatorPairBenchmarkTests(unittest.TestCase):
    def parameter_root(self, directory):
        root = Path(directory)
        (root / 'configs').mkdir()
        source = Path(__file__).resolve().parents[1] / 'configs'
        for name in ('isa.json', 'forwarding.json', 'InitiationInterval.json',
                     'execution_resources.json', 'timing_overrides.json'):
            shutil.copyfile(source / name, root / 'configs' / name)
        return root

    def test_rule_matching_measurements_keep_sparse_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.parameter_root(directory)
            measured = dict(isa={}, ii={'VCI.int32': {'VMUL.fp32': 1}},
                            forwarding={'VADD.fp32': {'VMUL.fp32': 3}})
            _, conflicts = PARAMETERS.apply_parameters(measured, root, True)
            self.assertEqual(conflicts, [])
            overrides = json.loads((root / 'configs/timing_overrides.json').read_text())
            self.assertNotIn('VMUL.fp32', overrides['forwarding'].get('VADD.fp32', {}))
            self.assertNotIn('VCI.int32', overrides['ii'])
            measured['forwarding']['VADD.fp32']['VMUL.fp32'] = 4
            _, conflicts = PARAMETERS.apply_parameters(measured, root, True)
            self.assertEqual(conflicts, [])
            overrides = json.loads((root / 'configs/timing_overrides.json').read_text())
            self.assertEqual(overrides['forwarding']['VADD.fp32']['VMUL.fp32'], 4)
            table = json.loads((root / 'configs/forwarding.json').read_text())['forwarding']
            self.assertNotIn('VMUL.fp32', table.get('VADD.fp32', {}))

    def test_recalibrating_an_exception_requires_the_reported_replace_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.parameter_root(directory)
            measured = dict(isa={}, ii={'VEXP.fp32': {'VDIV.fp32': 4}}, forwarding={})
            _, conflicts = PARAMETERS.apply_parameters(measured, root, True)
            self.assertEqual(conflicts[0]['configured'], 6)
            overrides = json.loads((root / 'configs/timing_overrides.json').read_text())
            self.assertEqual(overrides['ii']['VEXP.fp32']['VDIV.fp32'], 6)
            _, conflicts = PARAMETERS.apply_parameters(measured, root, True,
                replace=('ii.VEXP.fp32->VDIV.fp32',))
            self.assertEqual(conflicts, [])
            overrides = json.loads((root / 'configs/timing_overrides.json').read_text())
            self.assertNotIn('VDIV.fp32', overrides['ii']['VEXP.fp32'])

    def test_grouped_pair_with_missing_self_ii_is_saved_as_an_exception(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.parameter_root(directory)
            path = root / 'configs/InitiationInterval.json'
            document = json.loads(path.read_text())
            del document['InitiationInterval']['VCI.int32']['VCI.int32']
            path.write_text(json.dumps(document))
            measured = dict(isa={}, ii={'VCI.int32': {'VADD.fp32': 2}}, forwarding={})
            _, conflicts = PARAMETERS.apply_parameters(measured, root, True)
            self.assertEqual(conflicts, [])
            overrides = json.loads((root / 'configs/timing_overrides.json').read_text())
            self.assertEqual(overrides['ii']['VCI.int32']['VADD.fp32'], 2)

    def test_inventory_distinguishes_rules_exceptions_and_memory_pairs(self):
        from core.params.database import ParamDB
        db = ParamDB(base_dir=Path(__file__).resolve().parents[1])
        self.assertEqual(INVENTORY.timing_resolution(db, 'ii', 'VCI.int32', 'VMUL.fp32')[0], 'resource_rule')
        self.assertEqual(INVENTORY.timing_resolution(db, 'ii', 'VEXP.fp32', 'VDIV.fp32')[0], 'override')
        self.assertEqual(INVENTORY.timing_resolution(db, 'forwarding', 'VADD.fp32', 'VMUL.fp32')[0], 'resource_rule')
        self.assertEqual(INVENTORY.timing_resolution(db, 'forwarding', 'VLDS.fp32', 'VADD.fp32')[0], 'exact')

    def test_predicate_golden_uses_sparse_dtype_bits(self):
        raw = PROBES.reference("PSET_B32.b32", b"", b"", 0, 2)
        self.assertEqual(int.from_bytes(raw, "little"), 0x1111)
        self.assertEqual(INVENTORY.identity(dict(definition_id="x", iteration_path=[
            dict(loop_id="outer", iteration=2), dict(loop_id="inner", iteration=3)])),
            ("x", (("outer", 2), ("inner", 3))))

    def test_mixed_golden_keeps_integer_indices_and_guards_exact(self):
        case = dict(output_offset=0, output_regions=[dict(offset=0, bytes=4, dtype="fp32"),
                                                    dict(offset=4, bytes=4, dtype="uint32")])
        golden = bytearray(4096)
        golden[:4] = PROBES.compute.encode([1.0], "fp32")
        actual = bytearray(golden)
        actual[:4] = PROBES.compute.encode([1.000001], "fp32")
        self.assertEqual(PROBES.validate_regions(actual, golden, case), [])
        actual[4], actual[100] = 1, 1
        self.assertEqual(PROBES.validate_regions(actual, golden, case), [4, 100])

    def test_scalar_broadcast_probe_waits_for_scalar_dma(self):
        source, _, _, _ = PROBES.make_batch([dict(previous_key="VDUPS.fp32",
            current_key="VADD.fp32", kind="ii", replica=0, order="paired")])
        self.assertIn("wait_flag(PIPE_MTE2,PIPE_S", source)
        self.assertIn("*((__ubuf__ float *)", source)

    def test_interval_needs_repeats_both_ports_and_no_raw(self):
        self.assertEqual(ANALYSIS.derive([interval()], Database())[0]["ii"], {})
        self.assertEqual(ANALYSIS.derive([interval(), interval()], Database())[0]["ii"],
                         {"VADD.fp32": {"VEXP.fp32": 1}})
        row = interval()
        row["forwarding_observations"] = [dict(producer_id=1, consumer_id=2)]
        self.assertEqual(ANALYSIS.derive([row, row], Database())[0]["ii"], {})

    def test_self_interval_or_completion_collision_cannot_set_cross_interval(self):
        row = interval(3)
        row["events"].append(dict(id=90, port=0, form_key="VEXP.fp32", cycle=9, done=25))
        self.assertEqual(ANALYSIS.derive([row, row], Database())[0]["ii"], {})
        row = interval(3)
        row["all_compute_events"] = row["events"] + [dict(id=90, port=0, cycle=8, done=17)]
        self.assertEqual(ANALYSIS.derive([row, row], Database())[0]["ii"], {})

    def test_pair_own_completion_constraint_is_recorded_as_effective_interval(self):
        row = interval(2)
        for event in row["events"]:
            if event["form_key"] == "VADD.fp32":
                event["done"] = 17
        parameters, summaries = ANALYSIS.derive([row, row], Database())
        self.assertEqual(parameters["ii"], {"VADD.fp32": {"VEXP.fp32": 2}})
        self.assertTrue(all(value["preceding_pair_completion_conflict"]
                            for value in summaries[0]["observations"]))

    def test_hardware_forwarding_readiness_can_precede_source_completion(self):
        row = interval(2)
        for gap in row["independent_same_port_gaps"]:
            gap["sources_done_before_previous"] = False
        for event in row["events"]:
            event["hardware_source_ready_cycle"] = 11
        self.assertEqual(ANALYSIS.derive([row, row], Database())[0]["ii"],
                         {"VADD.fp32": {"VEXP.fp32": 2}})
        for event in row["events"]:
            event["hardware_source_ready_cycle"] = 12
        self.assertEqual(ANALYSIS.derive([row, row], Database())[0]["ii"], {})

    def test_forwarding_requires_all_operand_positions_and_other_sources_ready(self):
        self.assertEqual(ANALYSIS.derive_forwarding([forwarding(), forwarding()])[0], {})
        rows = [forwarding("Vn"), forwarding("Vn"), forwarding("Vm", 3), forwarding("Vm", 3)]
        self.assertEqual(ANALYSIS.derive_forwarding(rows)[0], {"VDUPS.fp32": {"VADD.fp32": 3}})
        rows[2]["selected_forwarding"][0]["other_sources_done_before_producer"] = False
        self.assertEqual(ANALYSIS.derive_forwarding(rows)[0], {})

    def test_load_queuing_cannot_become_forwarding(self):
        rows = [forwarding("Vn"), forwarding("Vn"), forwarding("Vm"), forwarding("Vm")]
        for row in rows:
            row["events"][0]["lsu_start"] = 12
        self.assertEqual(ANALYSIS.derive_forwarding(rows)[0], {})


if __name__ == "__main__":
    unittest.main()
