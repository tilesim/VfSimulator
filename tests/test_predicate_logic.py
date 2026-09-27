import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from api.frontend import canonical_vf_info_to_dict
from api.frontend.instruction_catalog import DEFAULT_INSTRUCTION_CATALOG
from api.simulator_costmodel import CoreVfCostModel
from core.param_db import ParamDB
from tests.test_predicate_registers import parse


ROOT = Path(__file__).resolve().parents[1]


class PredicateLogicTest(unittest.TestCase):
    def run_program(self, body):
        vf = parse(body)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = CoreVfCostModel(out_dir=str(root / "python")).run_vf_info(vf)
            self.assertLess(result["vf_end_cycle"], 1000)
            rows = [json.loads(line) for line in
                    (root / "python/start_by_cycle.json").read_text().splitlines()]
            runner = os.environ.get("VFSIM_NATIVE_RUNNER")
            if runner:
                path = root / "input.json"
                path.write_text(json.dumps(canonical_vf_info_to_dict(vf)))
                process = subprocess.run(
                    [runner, "--trace", str(path), "--out-dir", str(root / "native"),
                     "--max-cycles", "1000"], capture_output=True, text=True)
                self.assertEqual(process.returncode, 0, process.stderr)
                self.assertIn(f"vfEndCycle={result['vf_end_cycle']}", process.stdout)
                native = [json.loads(line) for line in
                          (root / "native/start_by_cycle.json").read_text().splitlines()]
                key = lambda data: sorted((r["inst_id"], r["op"], r["cy"]) for r in data)
                self.assertEqual(key(rows), key(native))
            return sorted(rows, key=lambda row: row["stream_seq"])

    def test_all_compare_forms_fp32(self):
        db = ParamDB(base_dir=str(ROOT))
        for family in ("vcmp", "vcmps"):
            for condition in ("eq", "ne", "gt", "ge", "lt", "le"):
                op = f"{family}_{condition}"
                with self.subTest(op=op):
                    rhs = "0.0f" if family == "vcmps" else "x"
                    rows = self.run_program(
                        "mask=pset_b32(PAT_ALL);vlds(x,a,0,NORM);"
                        f"{op}(p,x,{rhs},mask);vsel(y,x,x,p);"
                        "vsts(y,out,0,NORM_B32,mask);")
                    compare = next(r for r in rows if r["op"] == op.upper())
                    select = next(r for r in rows if r["op"] == "VSEL")
                    self.assertEqual(select["preg_src"][-1], compare["preg_dst"][0])
                    profile = db.resolve_inst(op.upper(), "fp32", "fp32")
                    self.assertEqual(profile.latency, 6)
                    self.assertEqual(profile.dispatch_exu, "EXU01")

    def test_logic_loop_carried_and_three_predicate_sources(self):
        db = ParamDB(base_dir=str(ROOT))
        for op in ("pand", "por"):
            rows = self.run_program(
                "mask=pset_b32(PAT_ALL);p=pset_b32(PAT_VL32);"
                "vlds(x,a,0,NORM);vector_bool q;vcmp_gt(q,x,x,mask);"
                f"for(int i=0;i<4;++i){{{op}(p,p,q,mask);}}"
                "vsel(y,x,x,p);vsts(y,out,0,NORM_B32,mask);")
            logic = [r for r in rows if r["op"] == op.upper()]
            self.assertEqual(len(logic), 4)
            producer = [r for r in rows if r["op"] == "PSET_B32"][1]
            for row in logic:
                self.assertEqual(len(row["preg_src"]), 3)
                self.assertTrue(all(p.startswith("pred") for p in row["preg_src"] + row["preg_dst"]))
                self.assertEqual(row["preg_src"][0], producer["preg_dst"][0])
                if producer["op"] == op.upper():
                    self.assertGreaterEqual(row["cy"], producer["cy"] + 4)
                producer = row
            select = next(r for r in rows if r["op"] == "VSEL")
            self.assertEqual(select["preg_src"][-1], logic[-1]["preg_dst"][0])
            self.assertEqual(db.resolve_inst(op.upper(), "b8", "fp32").latency, 7)
            self.assertEqual(db.get_forwarding_cycles(
                op.upper(), "VSEL", producer_form="b8", consumer_form="fp32"), 4)

    def test_invalid_operands(self):
        for body in ("pand(p,x,p,mask);", "por(p,p,mask);", "pand(p,p,p,mask,MODE_MERGING);"):
            with self.subTest(body=body), self.assertRaises(ValueError):
                parse("mask=pset_b32(PAT_ALL);p=pset_b32(PAT_ALL);" + body)

    def test_movvp_vector_to_predicate_loop(self):
        rows = self.run_program(
            "mask=pset_b32(PAT_ALL);vector_u32 bits;vlds(x,a,0,NORM);"
            "for(int i=0;i<4;++i){vdup(bits,0xffffffffu,mask,MODE_ZEROING);"
            "movvp(p,bits,0);vsel(y,x,x,p);vsts(y,out,64*i,NORM_B32,mask);}")
        moves = [r for r in rows if r["op"] == "MOVVP"]
        duplicates = [r for r in rows if r["op"] == "VDUP"]
        selects = [r for r in rows if r["op"] == "VSEL"]
        self.assertEqual(len(moves), 4)
        for producer, move, consumer in zip(duplicates, moves, selects):
            self.assertEqual(move["preg_src"], producer["preg_dst"])
            self.assertEqual(len(move["preg_dst"]), 1)
            self.assertTrue(move["preg_dst"][0].startswith("pred"))
            self.assertEqual(consumer["preg_src"][-1], move["preg_dst"][0])
            self.assertGreaterEqual(consumer["cy"], move["cy"] + 13)
        db = ParamDB(base_dir=str(ROOT))
        self.assertEqual(db.resolve_inst("MOVVP", "uint32", "fp32").latency, 16)
        self.assertEqual(db.get_forwarding_cycles(
            "MOVVP", "VSEL", producer_form="uint32", consumer_form="fp32"), 13)

    def test_movvp_rejects_unsupported_forms_and_parts(self):
        for declaration, call in (
            ("vector_u16 bits;", "movvp(p,bits,16);"),
            ("vector_f32 bits;", "movvp(p,bits,0);"),
            ("vector_u32 bits;", "movvp(p,bits,32);"),
            ("vector_u32 bits;", "movvp(p,bits,-1);"),
            ("vector_u32 bits;", "movvp(p,mask,0);"),
            ("vector_u32 bits;", "movvp(x,bits,0);"),
        ):
            with self.subTest(call=call, declaration=declaration), self.assertRaises(ValueError):
                parse("mask=pset_b32(PAT_ALL);" + declaration + call)
        spec = DEFAULT_INSTRUCTION_CATALOG.lookup("MOVVP")
        self.assertIn("31", spec.operands[2].allowed_values)

    def test_movvp_b16_boundaries_and_forwarding(self):
        for part in (0, 15):
            rows = self.run_program(
                "mask=pset_b16(PAT_ALL);vector_u16 bits;vlds(x,a,0,NORM);"
                "vdup(bits,0xffffu,mask,MODE_ZEROING);"
                f"movvp(p,bits,{part});vsel(y,x,x,p);vsts(y,out,0,NORM_B32,mask);")
            move = next(r for r in rows if r["op"] == "MOVVP")
            producer = next(r for r in rows if r["op"] == "VDUP")
            consumer = next(r for r in rows if r["op"] == "VSEL")
            self.assertEqual(move["preg_src"], producer["preg_dst"])
            self.assertEqual(consumer["preg_src"][-1], move["preg_dst"][0])
            self.assertGreaterEqual(consumer["cy"], move["cy"] + 13)
        db = ParamDB(base_dir=str(ROOT))
        self.assertEqual(db.resolve_inst("MOVVP", "uint16", "fp32").latency, 16)
        self.assertEqual(db.get_forwarding_cycles(
            "MOVVP", "VSEL", producer_form="uint16", consumer_form="fp32"), 13)
