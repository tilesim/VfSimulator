"""Numerical and dependency guards for pair-result benchmark extraction."""
import importlib
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1]/"skills/vfsim-instruction-benchmark/scripts"
with patch.object(sys, "path", [str(SCRIPTS), *sys.path]):
    PROBE = importlib.import_module("vector_structural")
    COMPUTE = importlib.import_module("vector_compute")
    ANALYSIS = importlib.import_module("analyze_vector_structural")


class StructuralBenchmarkAnalysisTest(unittest.TestCase):
    def test_subtraction_carry_is_no_borrow_and_inverts_carry_input(self):
        a = struct.pack("<64I", *([0, 1]*32))
        b = struct.pack("<64I", *([1]*64))
        result, flags = PROBE.pair_result("vsubc", a, b, "uint32")
        self.assertEqual(struct.unpack_from("<2I", result), (0xffffffff, 0))
        self.assertEqual(int.from_bytes(flags, "little") & 0xff, 0x10)
        result, flags = PROBE.pair_result("vsubcs", a, b, "uint32", carry=[False]*64)
        self.assertEqual(struct.unpack_from("<2I", result), (0xfffffffe, 0xffffffff))
        self.assertEqual(int.from_bytes(flags, "little") & 0xff, 0)

    def test_integer_reductions_widen_before_accumulation(self):
        raw = struct.pack("<128h", *([30000]*128))
        self.assertEqual(struct.unpack("<i", PROBE.reduce_result("vcadd", raw, "int16", 128)), (3840000,))
        self.assertEqual(struct.unpack("<8i", PROBE.reduce_result("vcgadd", raw, "int16", 128)), (480000,)*8)
        _, data, golden, _ = PROBE.make_batch([{"api":"vcadd", "dtype":"int16"}])
        self.assertEqual(golden[4:512], data[4:512])

    def test_implicit_second_result_and_old_carry_are_dependencies(self):
        for op, first_args, second_args, producer_role, consumer_role in [
            ("RV_VMULL", "Vd[2], Vn[0], Vm[1], Pg[0]", "Vd[4], Vn[3], Vm[1], Pg[0]", "Vd1", "Vn"),
            ("RV_VADDCS", "Vd[2], Vn[0], Vm[1], Pd[1], Pg[0]", "Vd[4], Vn[0], Vm[1], Pd[1], Pg[0]", "Pd", "Pd_in"),
        ]:
            with self.subTest(op=op), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                rows = [(1,1,"RV_PSET","Pd[0]",5), (1,2,"RV_PSET","Pd[1]",5),
                        (1,3,"RV_VLDI","Vd[0]",6), (1,4,"RV_VLDI","Vd[1]",6),
                        (10,5,op,first_args,18), (14,6,op,second_args,22)]
                def line(c, i, name, args):
                    return f"[info] [{c}] (PC: 0x{0x1000+4*i:x}) (ID: {i}) {name} {args}\n"
                (root/"output.bin").write_bytes(bytes(4096))
                (root/"core0.veccore0.instr_popped_log.dump").write_text("VF addr: 0x1000,instr_num: 0x8\n"+
                    "".join(line(c,i,name,args) for c,i,name,args,_ in rows))
                (root/"core0.veccore0.instr_log.dump").write_text(
                    "".join(line(done,i,name,args) for _,i,name,args,done in rows))
                (root/"core0.veccore0.rvec.EXU.dump").write_text(
                    "".join(f"[PERF] [{c+1}] instr_name {name} instr_id {i} exu_id:0\n" for c,i,name,_,_ in rows if name==op))
                contracts = [{"api":op[3:].lower(), "dtype":"uint32", "output_offset":0,
                              "output_bytes":3584, "guard_offset":3584, "guard_bytes":512, "target_count":2}]
                record = COMPUTE.analyze(root, contracts, bytes(4096))[0]
                edge = next(e for e in record["forwarding_observations"] if e["producer_id"]==5 and e["consumer_id"]==6)
                self.assertEqual((edge["producer_role"], edge["role"], edge["gap"]), (producer_role, consumer_role, 4))
                damaged = bytearray(4096); damaged[-1] = 1
                (root/"output.bin").write_bytes(damaged)
                self.assertFalse(COMPUTE.analyze(root, contracts, bytes(4096))[0]["guards_passed"])

    def test_forwarding_requires_both_results_at_both_source_positions(self):
        row = {"api":"vmull", "dtype":"uint32", "passed":True, "single_op_sample":True,
               "run":"probe", "events":[{"latency":8,"port":0},{"latency":8,"port":1}],
               "independent_same_port_gaps":[], "forwarding_observations":[]}
        for path in ["Vd->Vn", "Vd->Vm", "Vd1->Vn"]:
            producer, consumer = path.split("->")
            row["forwarding_observations"].append({"producer_op":"RV_VMULL", "consumer_op":"RV_VMULL",
                "producer_role":producer, "role":consumer, "gap":4, "other_sources_ready_at_producer_issue":True})
        self.assertNotIn("VMULL.uint32", ANALYSIS.derive([row])["forwarding"])
        row["forwarding_observations"].append({**row["forwarding_observations"][0], "producer_role":"Vd1", "role":"Vm", "gap":5})
        self.assertEqual(ANALYSIS.derive([row])["forwarding"]["VMULL.uint32"], {"VMULL.uint32":5})

    def test_unreported_carry_dependency_cannot_calibrate_zero_forwarding(self):
        edges=[]
        for path in ["Vd->Vn", "Vd->Vm", "Pd->Pg", "Pd->Pd_in"]:
            producer,consumer=path.split("->")
            edges.append({"producer_op":"RV_VADDCS", "consumer_op":"RV_VADDCS",
                          "producer_role":producer, "role":consumer,
                          "gap":0 if consumer=="Pd_in" else 4,
                          "other_sources_ready_at_producer_issue":True,
                          "dependency_reported_by_dispatch":consumer!="Pd_in"})
        row={"api":"vaddcs", "dtype":"uint32", "passed":True, "single_op_sample":True,
             "run":"probe", "events":[{"latency":7,"port":0},{"latency":7,"port":1}],
             "independent_same_port_gaps":[], "forwarding_observations":edges}
        report=ANALYSIS.derive([row])
        self.assertNotIn("VADDCS.uint32",report["forwarding"])
        self.assertEqual(report["forms"][0]["untracked_dependency_observations"], {"Pd->Pd_in":{0:1}})


if __name__ == "__main__":
    unittest.main()
