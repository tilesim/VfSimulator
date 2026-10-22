"""Canonical JSON input validation and serialization."""
from .adapter import CanonicalJsonVfInfoAdapter
from .serialization import canonical_vf_info_from_dict, canonical_vf_info_to_dict

__all__ = [
    "CanonicalJsonVfInfoAdapter", "canonical_vf_info_from_dict", "canonical_vf_info_to_dict",
]
