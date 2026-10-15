"""Runtime parameter lookup and caches; configuration I/O lives in loader.py."""
from __future__ import annotations
from dataclasses import InitVar, dataclass
from pathlib import Path
from copy import deepcopy
from typing import Any, Dict, Optional
from .types import InstructionProfile, LoadedParameters
from .loader import load_parameters, merge_parameters
from .uarch import allowed_exu_ports
from .fallback import compatible_form, form_candidates, pair_key_candidates, record_warning, warning_list, fallback_op_class, missing_instruction, missing_forwarding, missing_ii

@dataclass
class ParamDB:
    base_dir: str | None = None
    isa_path: str | None = None
    uarch_path: str | None = None
    forwarding_path: str | None = None
    ii_path: str | None = None
    parameters: InitVar[LoadedParameters | None] = None

    def __post_init__(self, parameters):
        self.base_dir = str(Path(self.base_dir or Path(__file__).resolve().parents[2]))
        data = parameters if parameters is not None else load_parameters(
            self.base_dir,
            isa_path=self.isa_path,
            uarch_path=self.uarch_path,
            forwarding_path=self.forwarding_path,
            ii_path=self.ii_path
        )
        self._isa = data.isa
        self._uarch = data.uarch
        self.base_uarch = data.base_uarch
        self.vf_timing = data.vf_timing
        self._defaults = data.isa.get('defaults', {})
        self._insts = data.isa.get('instructions', {})
        self._isa_schema_version = data.isa.get('schema_version', 1)
        self._fwd_default_offset = data.forwarding.get('default', data.forwarding.get('defaults', 3))
        self._fwd_table = data.forwarding.get('forwarding', {})
        self._ii_default = data.initiation_interval.get('defaults', 1)
        self._ii_table = data.initiation_interval.get('InitiationInterval', {})
        self._warnings = {}
        self._inst_param_templates = {}
        self._inst_param_cache = {}
        self._profile_cache = {}
        self._profiles_by_id = {}
        self._forwarding_profile_cache = {}
        self._ii_profile_cache = {}
        self._next_profile_id = 0
        self._prepare_inst_param_templates()

    def record_warning(self, kind, **payload):
        record_warning(self._warnings, kind, **payload)

    def get_warnings(self):
        return warning_list(self._warnings)

    def _fallback_inst_form_params(self, op, form=None, dtype=None, *, kind):
        op = str(op or '').upper()
        form = self._normalize_form_key(op, form) or self._dtype_to_form(dtype)
        return missing_instruction(self._defaults, op, form, dtype, self._warnings, kind)

    def _prepare_inst_param_templates(self) -> None:
        """Merge all declared forms once; fallback forms remain lazy."""
        if not self._is_v2_isa():
            return
        for raw_op, raw_node in self._insts.items():
            op = str(raw_op).upper()
            if not isinstance(raw_node, dict):
                continue
            forms = raw_node.get('forms', {}) or {}
            if not isinstance(forms, dict):
                continue
            op_defaults = {k: v for k, v in raw_node.items() if k != 'forms'}
            base = merge_parameters(self._defaults, op_defaults)
            for raw_form, raw_params in forms.items():
                form = str(raw_form)
                if not isinstance(raw_params, dict):
                    continue
                merged = dict(base)
                compatible = compatible_form(form)
                if compatible and compatible != form and (compatible in forms):
                    compatible_params = forms.get(compatible, {})
                    if isinstance(compatible_params, dict):
                        merged = merge_parameters(merged, compatible_params)
                merged = merge_parameters(merged, raw_params)
                merged.update({
                    'op': op,
                    'form': form,
                    'resolved_form': form,
                    'dtype': str(merged.get('dtype') or form)
                })
                self._inst_param_templates[op, form] = merged

    def get_uarch(self) -> Dict[str, Any]:
        """Return uarch dict as-is."""
        return dict(self._uarch)

    def get_defaults(self) -> Dict[str, Any]:
        """Return ISA defaults section (may be empty)."""
        return dict(self._defaults)

    def get_inst(self, op: str, dtype: str='fp32') -> Dict[str, Any]:
        """
        Return instruction parameters for given op and dtype.
        Result is merged with ISA defaults (defaults -> inst specific).
        """
        if self._is_v2_isa():
            return self.get_inst_form(op, form=dtype, dtype=dtype)
        opu = op.upper()
        node = self._insts.get(opu, {})
        if not isinstance(node, dict) or dtype not in node:
            return self._fallback_inst_form_params(opu, form=dtype, dtype=dtype, kind='unsupported_isa_op')
        inst_params = node.get(dtype, {}) or {}
        if not isinstance(inst_params, dict):
            raise TypeError(f'Bad schema for {opu}/{dtype}: expected dict, got {type(inst_params)}')
        merged = merge_parameters(self._defaults, inst_params)
        merged['op'] = opu
        merged['dtype'] = dtype
        return merged

    def _is_v2_isa(self) -> bool:
        return self._isa_schema_version >= 2

    @staticmethod
    def _normalize_form_key(op: str, form: Optional[str]) -> str:
        opu = op.upper()
        if form is None or str(form) == '':
            return ''
        f = str(form)
        if '.' in f or ':' in f:
            sep = '.' if '.' in f else ':'
            lhs, rhs = f.split(sep, 1)
            if lhs.upper() == opu:
                return rhs
        return f

    @staticmethod
    def _dtype_to_form(dtype: Optional[str]) -> str:
        return str(dtype or 'fp32')

    @staticmethod
    def _legacy_vcvt_form(op: str) -> Optional[str]:
        mapping = {
            'VCVT_F32_TO_F16': 'f32_to_f16',
            'VCVT_F16_TO_F32': 'f16_to_f32',
            'VCVT_F32_TO_S32': 'f32_to_s32',
            'VCVT_S32_TO_F32': 's32_to_f32'
        }
        return mapping.get(op.upper())

    def _select_v2_form(
        self,
        op: str,
        form: Optional[str]=None,
        dtype: Optional[str]=None,
        *,
        allow_fallback: bool=True
    ) -> str:
        opu = op.upper()
        node = self._insts.get(opu, {})
        forms = node.get('forms', {}) if isinstance(node, dict) else {}
        if not isinstance(forms, dict):
            if allow_fallback:
                return self._normalize_form_key(opu, form) or self._dtype_to_form(dtype)
            raise KeyError(f'Instruction has no forms: op={opu}')
        candidates = []
        normalized = self._normalize_form_key(opu, form)
        if normalized:
            candidates.extend(form_candidates(normalized))
        elif dtype is not None:
            candidates.extend(form_candidates(self._dtype_to_form(dtype)))
        legacy = self._legacy_vcvt_form(opu)
        if legacy:
            candidates.append(legacy)
        for candidate in candidates:
            if candidate in forms:
                return candidate
        if allow_fallback:
            return candidates[0] if candidates else self._dtype_to_form(dtype)
        raise KeyError(f'Instruction form not found: op={opu}, form={form}, dtype={dtype}')

    def _v2_inst_form_params(
        self,
        op: str,
        form: str,
        dtype: Optional[str],
        *,
        requested_form: Optional[str]=None
    ) -> Dict[str, Any]:
        opu = op.upper()
        node = self._insts.get(opu, {})
        if not isinstance(node, dict):
            raise KeyError(f'Instruction not found: op={opu}')
        forms = node.get('forms', {}) or {}
        inst_params = forms.get(form, {}) if isinstance(forms, dict) else {}
        if not isinstance(inst_params, dict):
            raise TypeError(f'Bad schema for {opu}.{form}: expected dict, got {type(inst_params)}')
        normalized_requested = self._normalize_form_key(opu, requested_form) or form
        template = self._inst_param_templates.get((opu, form))
        if template is not None:
            merged = dict(template)
        else:
            op_defaults = {k: v for k, v in node.items() if k != 'forms'}
            merged = merge_parameters(self._defaults, op_defaults)
            compatible = compatible_form(normalized_requested)
            if compatible and compatible != normalized_requested and (compatible in forms):
                compatible_params = forms.get(compatible, {}) if isinstance(forms, dict) else {}
                if not isinstance(compatible_params, dict):
                    raise TypeError(f'Bad schema for {opu}.{compatible}: expected dict, got {type(compatible_params)}')
                merged = merge_parameters(merged, compatible_params)
            merged = merge_parameters(merged, inst_params)
        merged['op'] = opu
        merged['form'] = normalized_requested
        merged['resolved_form'] = form
        merged['dtype'] = str(dtype or merged.get('dtype') or normalized_requested)
        if form != normalized_requested:
            self.record_warning(
                'compatible_isa_form_fallback',
                op=opu,
                requested_form=normalized_requested,
                used_form=form
            )
        return merged

    def get_inst_form(
        self,
        op: str,
        form: Optional[str]=None,
        dtype: Optional[str]=None,
        allow_fallback: bool=True
    ) -> Dict[str, Any]:
        """
        Return instruction parameters for a concrete form.

        v2 schema stores per-op forms such as VADD.fp32 or
        VCVT_F32_TO_F16.f32_to_f16.  For v1 configs this falls back to dtype.
        """
        opu = op.upper()
        normalized_form = self._normalize_form_key(opu, form)
        normalized_dtype = str(dtype or normalized_form or 'fp32')
        cache_key = (opu, normalized_form, normalized_dtype, bool(allow_fallback))
        cached = self._inst_param_cache.get(cache_key)
        if cached is not None:
            return deepcopy(cached)
        result: Dict[str, Any]
        if self._is_v2_isa():
            if opu not in self._insts:
                if allow_fallback:
                    result = self._fallback_inst_form_params(opu, form=form, dtype=dtype, kind='unsupported_isa_op')
                    self._inst_param_cache[cache_key] = dict(result)
                    return deepcopy(result)
                raise KeyError(f'Instruction not found: op={opu}')
            node = self._insts.get(opu, {})
            if not isinstance(node, dict):
                if allow_fallback:
                    result = self._fallback_inst_form_params(opu, form=form, dtype=dtype, kind='unsupported_isa_op')
                    self._inst_param_cache[cache_key] = dict(result)
                    return deepcopy(result)
                raise KeyError(f'Instruction not found: op={opu}')
            forms = node.get('forms', {}) or {}
            if not isinstance(forms, dict):
                raise TypeError(f'Bad schema for {opu}.forms: expected dict, got {type(forms)}')
            selected = self._select_v2_form(opu, form=form, dtype=dtype, allow_fallback=allow_fallback)
            if selected not in forms and allow_fallback:
                result = self._fallback_inst_form_params(opu, form=selected, dtype=dtype, kind='unsupported_isa_form')
                self._inst_param_cache[cache_key] = dict(result)
                return deepcopy(result)
            result = self._v2_inst_form_params(opu, selected, dtype=dtype, requested_form=form)
            self._inst_param_cache[cache_key] = dict(result)
            return deepcopy(result)
        legacy_dtype = str(dtype or form or 'fp32')
        return self.get_inst(opu, legacy_dtype)

    def resolve_inst(
        self,
        op: str,
        form: Optional[str]=None,
        dtype: Optional[str]=None,
        *,
        instruction_class: str | None=None
    ) -> InstructionProfile:
        opu = str(op or '').upper()
        requested_form = self._normalize_form_key(opu, form) or self._dtype_to_form(dtype)
        normalized_dtype = str(dtype or requested_form)
        key = (opu, requested_form, normalized_dtype, instruction_class)
        cached = self._profile_cache.get(key)
        if cached is not None:
            return cached
        params = self.get_inst_form(opu, form=requested_form, dtype=normalized_dtype)
        op_class = str(params.get('op_class', '')).upper()
        if op_class not in ('LOAD', 'STORE', 'COMPUTE'):
            unit = str(params.get('unit', '')).upper()
            lsu_op = str(params.get('lsu_op', '')).upper()
            if unit == 'LSU' and lsu_op in ('LOAD', 'STORE'):
                op_class = lsu_op
            elif unit == 'EXU':
                op_class = 'COMPUTE'
            else:
                op_class = fallback_op_class(opu)
        if instruction_class is not None:
            if instruction_class not in ('LOAD', 'STORE', 'COMPUTE'):
                raise ValueError('Unsupported instruction_class')
            op_class = instruction_class
        fu_type = str(params.get('EXU', 'ALU')).upper()
        if fu_type not in ('ALU', 'SFU'):
            fu_type = 'ALU'
        profile = InstructionProfile(
            profile_id=self._next_profile_id,
            op=opu,
            requested_form=requested_form,
            resolved_form=str(params.get('resolved_form', requested_form)),
            dtype=normalized_dtype,
            op_class=op_class,
            fu_type=fu_type,
            dispatch_exu=str(params.get('dispatch_exu', '')).upper(),
            latency=int(params.get('latency', 1)),
            allowed_exu_ports=allowed_exu_ports(str(params.get('dispatch_exu', '')).upper())
        )
        self._next_profile_id += 1
        self._profile_cache[key] = profile
        self._profiles_by_id[profile.profile_id] = profile
        return profile

    def has_inst(self, op: str, dtype: str='fp32') -> bool:
        opu = op.upper()
        node = self._insts.get(opu, {})
        if self._is_v2_isa():
            try:
                node = self._insts.get(opu, {})
                if not isinstance(node, dict):
                    return False
                forms = node.get('forms', {}) or {}
                if not isinstance(forms, dict):
                    return False
                selected = self._select_v2_form(opu, form=dtype, dtype=dtype, allow_fallback=False)
                return selected in forms
            except KeyError:
                return False
        return isinstance(node, dict) and dtype in node

    @staticmethod
    def _join_form_key(op: str, form: str) -> str:
        return f'{op.upper()}.{form}'

    @staticmethod
    def _split_form_key(op_or_key: str, form: Optional[str]) -> tuple[str, Optional[str]]:
        text = str(op_or_key)
        if form is not None:
            return (text.upper(), str(form))
        for sep in ('.', ':'):
            if sep in text:
                lhs, rhs = text.split(sep, 1)
                return (lhs.upper(), rhs)
        return (text.upper(), None)

    def get_forwarding_cycles(
        self,
        producer_op: str,
        consumer_op: str,
        dtype: str='fp32',
        producer_form: Optional[str]=None,
        consumer_form: Optional[str]=None
    ) -> int:
        producer = self.resolve_inst(producer_op, producer_form, dtype)
        consumer = self.resolve_inst(consumer_op, consumer_form, dtype)
        return self.get_forwarding_for_profiles(producer, consumer)

    def get_forwarding_for_profiles(self, producer: InstructionProfile, consumer: InstructionProfile) -> int:
        self._require_owned_profile(producer)
        self._require_owned_profile(consumer)
        key = (producer.profile_id, consumer.profile_id)
        cached = self._forwarding_profile_cache.get(key)
        if cached is not None:
            return cached
        value = self._compute_forwarding_cycles(
            producer.op,
            consumer.op,
            dtype=consumer.dtype,
            producer_form=producer.requested_form,
            consumer_form=consumer.requested_form
        )
        self._forwarding_profile_cache[key] = value
        return value

    def _require_owned_profile(self, profile: InstructionProfile) -> None:
        if self._profiles_by_id.get(profile.profile_id) is not profile:
            raise ValueError('InstructionProfile belongs to a different ParamDB instance')

    def _compute_forwarding_cycles(
        self,
        producer_op: str,
        consumer_op: str,
        dtype: str='fp32',
        producer_form: Optional[str]=None,
        consumer_form: Optional[str]=None
    ) -> int:
        """
        Forwarding cycles for COMPUTE->COMPUTE dependency.

        Lookup order:
          1) forwarding.json explicit table:
             forwarding[dtype][PRODUCER][CONSUMER] -> cycles
          2) fallback: max(0, latency(PRODUCER) - default_offset)
             where default_offset is forwarding.json["default"] (default 3 if file missing)
        """
        p, parsed_pf = self._split_form_key(producer_op, producer_form)
        c, parsed_cf = self._split_form_key(consumer_op, consumer_form)
        from api.common.instruction_catalog import DEFAULT_INSTRUCTION_CATALOG
        spec = DEFAULT_INSTRUCTION_CATALOG.lookup(p)
        if spec is not None and spec.forwarding_opcode is not None:
            return self._compute_forwarding_cycles(spec.forwarding_opcode, c, dtype, parsed_pf, parsed_cf)
        producer_form = parsed_pf
        consumer_form = parsed_cf
        if self._is_v2_isa():
            p_form = self._normalize_form_key(p, producer_form) or self._dtype_to_form(dtype)
            c_form = self._normalize_form_key(c, consumer_form) or self._dtype_to_form(dtype)
            p_key = self._join_form_key(p, p_form)
            c_key = self._join_form_key(c, c_form)
            for lookup_p, lookup_c, used_compatible in pair_key_candidates(p, p_form, c, c_form):
                prod_map = (self._fwd_table or {}).get(lookup_p, None)
                if isinstance(prod_map, dict) and lookup_c in prod_map:
                    value = max(0, int(prod_map[lookup_c]))
                    if used_compatible:
                        self.record_warning(
                            'compatible_forwarding_pair_fallback',
                            producer=p_key,
                            consumer=c_key,
                            used_producer=lookup_p,
                            used_consumer=lookup_c
                        )
                    return value
        else:
            prod_map = None
            prod_map = (self._fwd_table or {}).get(dtype, {}).get(p, None)
            if isinstance(prod_map, dict) and c in prod_map:
                return max(0, int(prod_map[c]))
        if self._is_v2_isa():
            producer_key = self._join_form_key(
                p,
                self._normalize_form_key(p, producer_form) or self._dtype_to_form(dtype)
            )
            producer_defaults = (self._fwd_table or {}).get(producer_key, {})
            if '*' in producer_defaults:
                return max(0, int(producer_defaults['*']))
        producer = self.get_inst_form(p, form=producer_form, dtype=dtype)
        consumer = self.get_inst_form(c, form=consumer_form, dtype=dtype)
        return missing_forwarding(producer, consumer, self._fwd_default_offset, self._warnings)

    def get_ii(
        self,
        prev_op: str,
        cur_op: str,
        dtype: str='fp32',
        prev_form: Optional[str]=None,
        cur_form: Optional[str]=None
    ) -> int:
        previous = self.resolve_inst(prev_op, prev_form, dtype)
        current = self.resolve_inst(cur_op, cur_form, dtype)
        return self.get_ii_for_profiles(previous, current)

    def get_ii_for_profiles(self, previous: InstructionProfile | None, current: InstructionProfile) -> int:
        self._require_owned_profile(current)
        if previous is None:
            return 1
        self._require_owned_profile(previous)
        key = (previous.profile_id, current.profile_id)
        cached = self._ii_profile_cache.get(key)
        if cached is not None:
            return cached
        value = self._compute_ii(
            previous.op,
            current.op,
            dtype=current.dtype,
            prev_form=previous.requested_form,
            cur_form=current.requested_form
        )
        self._ii_profile_cache[key] = value
        return value

    def _compute_ii(
        self,
        prev_op: str,
        cur_op: str,
        dtype: str='fp32',
        prev_form: Optional[str]=None,
        cur_form: Optional[str]=None
    ) -> int:
        """
        Initiation interval for compute issue on the same EXU.

        Semantics:
          cycle(cur_op) >= last_issue(prev_op_on_same_port) + II(prev_op, cur_op)

        Lookup order:
          1) Initiation_Interval.json explicit table:
             InitiationInterval[dtype][PREV_OP][CUR_OP] -> ii
          2) fallback: defaults value in Initiation_Interval.json (default 1 if file missing)
        """
        p, parsed_pf = self._split_form_key(prev_op, prev_form)
        c, parsed_cf = self._split_form_key(cur_op, cur_form)
        prev_form = parsed_pf
        cur_form = parsed_cf
        if self._is_v2_isa():
            p_form = self._normalize_form_key(p, prev_form) or self._dtype_to_form(dtype)
            c_form = self._normalize_form_key(c, cur_form) or self._dtype_to_form(dtype)
            p_key = self._join_form_key(p, p_form)
            c_key = self._join_form_key(c, c_form)
            for lookup_p, lookup_c, used_compatible in pair_key_candidates(p, p_form, c, c_form):
                prev_map = (self._ii_table or {}).get(lookup_p, None)
                if isinstance(prev_map, dict) and lookup_c in prev_map:
                    value = max(1, int(prev_map[lookup_c]))
                    if used_compatible:
                        self.record_warning(
                            'compatible_ii_pair_fallback',
                            prev=p_key,
                            cur=c_key,
                            used_prev=lookup_p,
                            used_cur=lookup_c
                        )
                    return value
        else:
            prev_map = None
            prev_map = (self._ii_table or {}).get(dtype, {}).get(p, None)
            if isinstance(prev_map, dict) and c in prev_map:
                return max(1, int(prev_map[c]))
        previous = self.get_inst_form(p, form=prev_form, dtype=dtype)
        current = self.get_inst_form(c, form=cur_form, dtype=dtype)
        return missing_ii(previous, current, self._warnings)
