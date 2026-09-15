import subprocess
import sys
from pathlib import Path

from api.frontend.serialization import canonical_vf_info_to_dict
from api.program_adapter import program_to_canonical
from tests.test_program_api import program
import vfsimulator as packaged
from vfsimulator.api.frontend.serialization import canonical_vf_info_to_dict as package_dict


def test_package_sources_are_synchronized():
    subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1]/'tools/sync_python_package.py'), '--check'], check=True)


def test_packaged_api_matches_root(tmp_path):
    root = program()
    def convert(n):
        if hasattr(n, 'body'):
            return packaged.VfSimLoop(n.count, [convert(x) for x in n.body], n.name, n.unroll)
        if hasattr(n, 'barrier'):
            return packaged.VfSimMembar(n.barrier)
        return packaged.VfSimInst(n.op, n.src, n.dst, n.form, n.config)
    p = packaged.VfSimProgram(body=[convert(x) for x in root.body], params=root.params,
        values={k: packaged.VfSimValue(v.value_id, v.storage.value, v.dtype, v.shape, v.storage_object_id, v.offsets) for k,v in root.values.items()})
    assert package_dict(packaged.program_to_canonical(p)) == canonical_vf_info_to_dict(program_to_canonical(root))
    from api.program_api import predict_from_program
    assert packaged.predict_from_program(p, out_dir=tmp_path/'package')['cycles'] == predict_from_program(root, out_dir=tmp_path/'root')['cycles']
