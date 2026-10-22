"""Parameter compatibility and explicit missing-entry policy."""
import json
from copy import deepcopy

from typing import Iterable


COMPATIBLE_FORM_FALLBACKS = {
    "b16": "fp16",
    "b32": "fp32",
}


def compatible_form(form: str | None) -> str | None:
    if form is None:
        return None
    return COMPATIBLE_FORM_FALLBACKS.get(str(form))


def form_candidates(form: str | None) -> list[str]:
    if form is None or str(form) == "":
        return []
    exact = str(form)
    compatible = compatible_form(exact)
    return [exact, compatible] if compatible and compatible != exact else [exact]


def pair_key_candidates(
    producer_op: str,
    producer_form: str,
    consumer_op: str,
    consumer_form: str,
) -> Iterable[tuple[str, str, bool]]:
    producer_forms = form_candidates(producer_form)
    consumer_forms = form_candidates(consumer_form)
    for p_form in producer_forms:
        for c_form in consumer_forms:
            used_compatible = p_form != producer_form or c_form != consumer_form
            yield f"{producer_op.upper()}.{p_form}", f"{consumer_op.upper()}.{c_form}", used_compatible


def record_warning(warnings, kind, **payload):
    key_items = []
    for key, value in sorted(payload.items()):
        if key in ("used_defaults", "sample_inst_ids"):
            continue
        if isinstance(value, (dict, list)):
            value = json.dumps(value, sort_keys=True, ensure_ascii=False)
        key_items.append((key, value))
    key = (kind, tuple(key_items))
    item = warnings.setdefault(key, {"kind": kind, **payload, "count": 0})
    item["count"] += 1


def warning_list(warnings):
    return sorted((dict(item) for item in warnings.values()),
                  key=lambda item: (str(item.get("kind", "")), str(item.get("op", "")), str(item.get("form", ""))))


def fallback_op_class(op):
    from vfsimulator.api.common.instruction_catalog import DEFAULT_INSTRUCTION_CATALOG

    spec = DEFAULT_INSTRUCTION_CATALOG.lookup(op)
    if spec is not None:
        return spec.instruction_class.value.upper()
    if op.startswith("VLD"):
        return "LOAD"
    if op.startswith("VST"):
        return "STORE"
    return "COMPUTE"


def missing_instruction(defaults, op, form, dtype, warnings, kind):
    op_class = fallback_op_class(op)
    params = deepcopy(defaults)
    params.update(op=op, form=form, dtype=str(dtype or form), op_class=op_class,
                  latency=9, pipeline_startup_cost=0, pipeline_drain_cost=0, throughput=1)
    if op_class == "COMPUTE":
        params.update(EXU="ALU", dispatch_exu="EXU01")
    record_warning(warnings, kind, op=op, form=form, used_defaults={
        "op_class": op_class, "latency": 9,
        **({"EXU": "ALU", "dispatch_exu": "EXU01"} if op_class == "COMPUTE" else {}),
    })
    return params


def missing_forwarding(producer, consumer, offset, warnings):
    latency = int(producer.get("latency", 0))
    if producer.get("op_class") == "LOAD" and consumer.get("op_class") == "STORE":
        cycles, rule = 6, "load_store_default"
    else:
        cycles, rule = max(0, latency-offset), "producer_latency_minus_default_offset"
    record_warning(warnings, "missing_forwarding_pair",
                   producer=f"{producer['op']}.{producer['form']}",
                   consumer=f"{consumer['op']}.{consumer['form']}",
                   producer_latency=latency, used_default=cycles, rule=rule)
    return cycles


def missing_ii(previous, current, warnings):
    prev_latency = int(previous.get("latency", 9))
    cur_latency = int(current.get("latency", 9))
    cycles = 2 if prev_latency-cur_latency == 1 else 1
    record_warning(warnings, "missing_ii_pair",
                   prev=f"{previous['op']}.{previous['form']}", cur=f"{current['op']}.{current['form']}",
                   prev_latency=prev_latency, cur_latency=cur_latency, used_default=cycles,
                   rule="prev_latency_minus_cur_latency_eq_1" if cycles==2 else "default_one")
    return cycles
