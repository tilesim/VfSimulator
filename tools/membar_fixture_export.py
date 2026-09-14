"""Test-only Canonical fixture encoder for membar cross-language checks."""
from dataclasses import asdict
from api.frontend.schema import CanonicalInstruction, CanonicalLoop, CanonicalMembar
from api.frontend.serialization import canonical_vf_info_from_dict


def canonical_vf_info_to_dict(vf):
    def node(value):
        result = asdict(value)
        if isinstance(value, CanonicalLoop):
            result['kind'] = 'loop'
            result['body'] = [node(child) for child in value.body]
        elif isinstance(value, CanonicalInstruction):
            result['kind'] = 'instruction'
        elif isinstance(value, CanonicalMembar):
            result['kind'] = 'membar'
        else:
            raise TypeError(type(value).__name__)
        return result
    result = asdict(vf)
    result['context'] = [node(child) for child in vf.context]
    assert canonical_vf_info_from_dict(result) == vf
    return result
