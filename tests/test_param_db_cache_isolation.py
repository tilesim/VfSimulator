"""Public config results must not expose objects shared by the JSON cache."""
import importlib
import json
from pathlib import Path

import pytest


@pytest.fixture(params=['core.param_db', 'vfsimulator.core.param_db'])
def db_module(request):
    module = importlib.import_module(request.param)
    module.clear_param_json_cache()
    yield module
    module.clear_param_json_cache()


@pytest.fixture(params=[1, 2])
def config_dir(request, tmp_path):
    configs = tmp_path/'configs'
    configs.mkdir()
    uarch = json.loads((Path(__file__).resolve().parents[1]/'configs/uarch.json').read_text())
    (configs/'uarch.json').write_text(json.dumps(uarch))
    params = {'latency': 4, 'nested': {'items': [3, 4]}}
    instruction = {'forms': {'fp32': params}} if request.param == 2 else {'fp32': params}
    (configs/'isa.json').write_text(json.dumps({
        'schema_version': request.param,
        'defaults': {'nested': {'items': [1, 2]}},
        'instructions': {'VADD': instruction},
    }))
    return tmp_path


def test_uarch_nested_mutation_is_isolated(db_module, config_dir):
    first = db_module.ParamDB(base_dir=str(config_dir))
    existing = db_module.ParamDB(base_dir=str(config_dir))
    path = config_dir/'configs/uarch.json'
    original = path.read_bytes()
    expected = first.get_uarch()
    returned = first.get_uarch()
    returned['membar_timing']['directions']['VST_VLD']['release_latency'] = 999
    returned['added'] = True
    for db in (first, existing, db_module.ParamDB(base_dir=str(config_dir))):
        assert db.get_uarch() == expected
    assert path.read_bytes() == original


@pytest.mark.parametrize('accessor,expected', [
    (lambda db: db.get_defaults(), [1, 2]),
    (lambda db: db.get_inst('VADD'), [3, 4]),
    (lambda db: db.get_inst_form('VADD', 'fp32'), [3, 4]),
    (lambda db: {'nested': db.get_inst_param('VADD', 'nested')}, [3, 4]),
    (lambda db: db.get_inst('UNKNOWN'), [1, 2]),
])
def test_nested_isa_results_are_isolated(db_module, config_dir, accessor, expected):
    first = db_module.ParamDB(base_dir=str(config_dir))
    existing = db_module.ParamDB(base_dir=str(config_dir))
    result = accessor(first)
    result['nested']['items'].append(999)
    for db in (first, existing, db_module.ParamDB(base_dir=str(config_dir))):
        assert accessor(db)['nested']['items'] == expected
