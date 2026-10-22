# Python 接口

`api/input_api.py::InputAPI` 接收输入并输出 `CanonicalVfInfo`。
`core/simulator.py::CoreVfCostModel` 使用该对象进行预测。

## 文件输入

```python
from api.input_api import InputAPI
from core.simulator import CoreVfCostModel

vf_info = InputAPI.load_json("VFtest/canonical/fixtures/v1_valid_loop.json")
result = CoreVfCostModel(out_dir=None).run_vf_info(vf_info)
print(result["cycles_executed"], result["vf_end_cycle"])
```

CCE 输入使用 `InputAPI.load_cce(path, kernel_name=None, loop_params=None)`。
`kernel_name` 选择 VF 函数，`loop_params` 提供循环次数等整数参数。
JSON 输入支持 Canonical schema v1/v2。所有正式输入出口执行一次通用语义校验。

## 程序化构造

`InputAPI.new_builder()` 返回 `VfInfoBuilder`。构造时提供值定义、producer 和
operand，`build()` 返回校验成功的程序。

```python
from api.input_api import InputAPI
from api.common.schema import CanonicalOperand, InstructionClass, OperandRole, StorageKind
from core.simulator import CoreVfCostModel

builder = InputAPI.new_builder(source={"name": "vector_add"})
for name, producer in (("lhs", None), ("rhs", None), ("sum", "inst.add")):
    builder.register_value(
        name, logical_id=name, storage=StorageKind.REGISTER,
        dtype="fp32", producer_node_id=producer,
    )
builder.add_instruction(
    "inst.add", opcode="VADD", instruction_class=InstructionClass.COMPUTE, form="fp32",
    inputs=(CanonicalOperand("lhs", OperandRole.SOURCE, "fp32"),
            CanonicalOperand("rhs", OperandRole.SOURCE, "fp32")),
    outputs=(CanonicalOperand("sum", OperandRole.DESTINATION, "fp32"),),
)
vf_info = builder.build()
cycles = CoreVfCostModel(out_dir=None).predict_vf_cycles(vf_info)
print(cycles)
```

Builder 的 `params` 保存整数参数，`uarch` 保存单次微架构标量覆盖，`source` 保存来源
元数据。UB 输入通过 `register_storage_object()` 声明存储对象，访存 operand 引用对象
并提供 `MemoryAccess`。循环通过 `loop()` 或 `add_loop()` 构造。

直接实例化 Canonical dataclass 的调用方使用 `InputAPI.from_canonical(raw_vf_info)`
接收对象。value、producer、operand 和循环携带关系由调用方显式提供。
修改对象内部 Mapping 后应重新通过该入口校验。

## 模型参数与结果

| 参数 | 用途 |
| --- | --- |
| `base_dir` | 配置与相对输出路径的根目录，默认仓库根目录 |
| `out_dir` | 文件输出目录，None 表示关闭输出 |
| `fallback_dtype` | 参数查询缺少形式信息时使用的默认 dtype，默认 fp32 |
| `theoretical_limit_vloop_only` | 启用 VLOOP 理论场景 |
| `theoretical_limit_direct_issue` | 启用直接发射理论场景 |

`predict_vf_cycles()` 返回 `vf_end_cycle` 整数；`run_vf_info()` 返回包含执行 cycle、
VF 结束 cycle、静态布局指令数和输出路径的字典。字段详见
[验证与精度](docs/validation.md)。
指令的 form 和数据 dtype 由 Canonical 描述；微架构覆盖由 `vf_info.uarch` 提供，
支持字段见 [配置参数](docs/configuration.md)。

## 校验与诊断

```python
from api.input_api import InputAPI
from api.common.schema import CanonicalVfInfo

raw_vf_info = CanonicalVfInfo(context=(), values={})
validation = InputAPI.validate_canonical_vf_info(raw_vf_info)
print(validation.ok, validation.errors)
vf_info = InputAPI.from_canonical(raw_vf_info)
```

检查接口返回 `ValidationResult`，包含结构化错误、警告及定位信息。
正式接收接口在语义错误时抛出 `VfInfoValidationError`，错误输入类型会触发 TypeError。
Core 检查当前执行能力和运行期约束，能力错误通过 `CanonicalCoreCompatibilityError`
等异常报告。

Canonical 字段和转换阶段见 [输入 API](docs/input_api.md)。
