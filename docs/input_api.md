# 输入 API 与 Canonical 数据

`api/input_api.py::InputAPI` 接收 CCE/DSL 文件、Canonical JSON 文件和
`CanonicalVfInfo` 对象。所有正式输入出口执行一次通用语义校验。
Core 接收通过 API 校验的 Canonical 对象，并检查模型执行能力。

## 公开接口

| 接口 | 用途 |
| --- | --- |
| `load_cce(path, kernel_name=None, loop_params=None)` | 读取 CCE，选择 VF scope，解析并构造 Canonical |
| `load_json(path)` | 检查 JSON Schema，反序列化并校验 Canonical |
| `from_canonical(vf_info)` | 校验直接构造的对象并返回原对象 |
| `new_builder(params=None, uarch=None, source=None)` | 创建 `VfInfoBuilder`，通过显式调用逐项构造 |
| `validate_canonical_vf_info(vf_info)` | 返回包含错误和警告的 `ValidationResult` |

Builder 的三个参数都使用关键字传入：`params` 提供循环次数等整数参数；`uarch`
提供本次预测的微架构标量覆盖；`source` 保存来源元数据。`new_builder()` 返回构造工具，
`builder.build()` 返回校验成功的 `CanonicalVfInfo`。

```python
from api.input_api import InputAPI
from core.simulator import CoreVfCostModel

vf_info = InputAPI.load_cce("cce_code/GeLU_poly.dsl")
result = CoreVfCostModel(out_dir="results/gelu").run_vf_info(vf_info)
print(result["vf_end_cycle"])
```

直接构造对象的调用方提供 value、producer、operand 和 loop-carried 关系，然后调用
`InputAPI.from_canonical()`。修改对象中的 Mapping 后，应重新通过该入口校验。

## CCE 转换流程

| 阶段 | 文件和函数 | 产物及作用 |
| --- | --- | --- |
| 源码和 scope 选择 | [source.py](../api/cce/source.py)、[parser.py](../api/cce/parser.py) 的 `_parse_cce_adapter_program` | 识别 VF 作用域和调用参数，确定所选函数 |
| 语法与参数绑定 | `parser.py::_VFScopeParser` | 构造 CCE 私有 `AdapterProgram`，绑定 Catalog 签名、dtype、访存、状态及循环 |
| 规范化 | [normalization.py](../api/cce/normalization.py) 的 `normalize_adapter_program` | 合并 value 声明，统一 form 和 operand 引用 |
| 值版本化 | [value_versioning.py](../api/cce/value_versioning.py) 的 `ValueVersioningPass.run` | 为静态写入建立 definition，构造 producer、alias 和循环 entry/back-edge/exit |
| 语义校验 | [validator.py](../api/common/validator.py) 的 `require_valid_canonical_vf_info` | 检查定义、作用域、签名、循环、访存和配置，返回 Canonical |

`cce/adapter.py` 组合这些阶段。规范化在 parser 输出时执行；版本化消费规范化后的私有 IR。
CCE parser 支持已定义的向量作用域语法，支持范围由 parser 和 Catalog 共同确定。

JSON adapter 检查文件格式并直接反序列化 Canonical。Builder 和直接对象使用显式定义，
调用方负责提供定义版本和循环携带关系。

## 共享数据类型

类型定义位于 [schema.py](../api/common/schema.py)。

| 类型 | 含义 |
| --- | --- |
| `CanonicalVfInfo` | 程序上下文、值表、UB 对象、参数、微架构覆盖和来源信息 |
| `CanonicalInstruction` | 指令 ID、opcode、类别、form、输入输出和属性 |
| `CanonicalValue` | 一次静态定义的身份、逻辑身份、存储类别、dtype、shape 和 producer |
| `CanonicalOperand` | 引用 value，声明 operand role、可选 dtype 和 memory access |
| `CanonicalStorageObject` | UB 存储对象的稳定身份及 shape |
| `MemoryAccess` | UB 对象、offset、读写方向、span、地址状态和更新量 |
| `CanonicalLoop` | 循环次数、unroll、body、迭代变量和携带定义 |
| `CanonicalMembar` | 屏障方向和指令流位置 |
| `SourceLocation` | 源文件、行、列等定位信息，供诊断追溯 |

`logical_id` 表示源码层的逻辑值，`definition_id` 标识一次静态定义；operand 的
`value_id` 引用该 definition。指令的 `form` 用于签名和时序查询，value 的 `dtype`
描述值类型，operand 的可选 `dtype` 描述本次使用。相关字段由 validator 检查一致性。
存储类别由 `StorageKind` 显式声明为 `Register`、`PredicateRegister`、`UB` 或 `Scalar`。
名称可自由选择，例如 `mem_result` 可以声明为 Register，`v_buffer` 可以声明为 UB。

`AffineExpression` 以常量和 `AffineTerm(variable_id, coefficient)` 表示地址表达式。
CCE 的 affine 解析位于 [affine.py](../api/cce/affine.py)。
`(a+b)*64+d*64` 可以表示为三个系数均为 64 的项；变量乘变量超出 affine 范围。
`MemoryAccess.offset` 和 `span` 使用内存元素单位，`post_update_delta_bytes` 使用字节单位。

schema v1/v2 的读写由 [serialization.py](../api/json/serialization.py)处理；输出版本
根据对象所需语义由 `emission_schema_version()` 选择。谓词和循环出口等语义使用 v2。

## Catalog 与诊断

[InstructionCatalog](../api/common/instruction_catalog.py)从
[instruction_catalog.json](../configs/instruction_catalog.json)创建每条指令的
`InstructionSpec`，提供签名、operand、form、mode、访存预算和对齐操作声明。
`FormRule` 决定 form 来自操作数类型、转换类型对或固定值；`OperandSpec` 声明参数位置、
方向、类型及允许值。`ArgumentKind.CONFIG` 表示模式和立即数配置，
`REGISTER_OR_SCALAR` 支持两种数据来源，`ALIGN_STATE` 表示特殊对齐状态。

[diagnostics.py](../api/common/diagnostics.py)定义结构化诊断、严重程度、校验结果和
`VfInfoValidationError`。校验失败的正式输入入口抛出该异常；单独检查函数返回诊断结果。

Core 的执行能力检查位于 `CoreLoweringPass.ensure_current_core_compatible()`。
其约束包括 CONTROL 指令形式、非最内层 unroll，以及次数与 unroll
整除关系。合法的 Canonical 数据需要同时满足所选执行模型的能力范围。

## C++ 输入接口

`native/api/input_api.h` 提供 `loadCce(path, kernelName, loopParams)`、`loadJson(path)`、
`fromCanonical(vf)` 和 `newBuilder(params, uarch, source)`。
`native/api/cce/` 按源码扫描、解析、规范化、仿射表达式和值版本化拆分，生成经过校验的 Canonical 对象。
`native/api/programmatic/builder.*` 提供 `registerStorageObject/registerValue`、`addInstruction/addMembar/addLoop`。
`builder.loop(definition)` 返回作用域守卫，正常结束提交 body，异常展开回滚声明和节点。
`encodeCanonicalVfInfoJson()` / `decodeCanonicalVfInfoJson()` 提供 JSON 编解码。
C++ 前端和 Builder 使用统一 Catalog 及 Canonical 校验，不依赖 Python 运行时。
