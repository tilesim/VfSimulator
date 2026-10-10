# Python 输入 API

`api/` 只处理三种输入：CCE/DSL 文件、Canonical JSON，以及直接构造的
`CanonicalVfInfo` 对象。所有输入使用同一份 Canonical 数据合同；Python
预测和执行准备位于 `core/`。

## 正式入口

- `InputAPI.load_cce(path, kernel_name=None, loop_params=None)`：解析并校验 CCE，返回 Canonical 对象。
- `InputAPI.load_json(path)`：校验 JSON Schema，反序列化并校验 Canonical 语义。
- `InputAPI.from_canonical(vf_info)`：校验直接构造的 Canonical 对象，成功后返回原对象。
- `InputAPI.new_builder(...)`：显式构造值定义、指令和循环；`build()` 返回经过校验的对象。
- `InputAPI.validate_canonical_vf_info(vf_info)`：返回结构化诊断，不修改输入。

```python
from api.input_api import InputAPI
from core.simulator import CoreVfCostModel

vf_info = InputAPI.load_cce("cce_code/GeLU_poly.dsl")
cycles = CoreVfCostModel().predict_vf_cycles(vf_info)
```

直接构造 dataclass 时，调用方必须提供完整的 definition、producer、operand
和 loop-carried 关系，并通过 `InputAPI.from_canonical(vf_info)` 校验后再交给 Core。
Builder 的 `build()` 已完成这一校验，可直接将结果交给 Core。Builder 不替调用方推断依赖。JSON 是这些对象的序列化
形式，支持 schema v1/v2；v2 表达谓词及循环仅出口定义等语义。

旧 `VFInfo`、旧 `program/src/dst` JSON 和离线迁移接口已删除。请使用 Canonical
JSON 或重新解析原始 CCE；API 不猜测格式，也不补造缺失的操作数。

## 目录边界

- `cce/`：CCE 专属的源码处理、私有 IR、规范化、affine 解析和值版本化。
- `json/`：Canonical JSON 格式校验、schema 和序列化。
- `programmatic/`：通过 Builder 显式构造 Canonical 对象。
- `common/`：三种输入共享的 Canonical 类型、语义校验、Catalog、符号和诊断。

`CanonicalVfInfo` 定义在 `common/schema.py`。它是共享合同，不需要单独的输入目录。
`common/` 不依赖 CCE、JSON 或程序化构造代码，JSON Schema 则属于 `json/` 的文件格式合同。
`input_api.py` 在顶层组合输入方式；没有新增转换管理器或旧路径转发模块。

## 转换流程和职责

```text
CCE -> cce/parser.py -> normalized AdapterProgram -> ValueVersioningPass
JSON -> JSON Schema -> deserialization
直接 Canonical 对象 / Builder.build()
                         |
        common/validator.py::require_valid_canonical_vf_info
            类型检查 -> 一次通用语义校验 -> 返回原对象
                         |
                    CanonicalVfInfo
                         |
     core.execution.prepare.CoreLoweringPass（仅执行能力检查和表示转换）
                         |
                 Core runtime preparation
```

| 文件 | 职责 |
| --- | --- |
| `input_api.py` | 三种输入的公开入口 |
| `cce/adapter.py` | 组合 CCE 解析、值版本化和输入校验 |
| `cce/source.py` | 源码读取、VF scope 提取和 token 工具 |
| `cce/parser.py` | 语法、作用域、Catalog 参数绑定及访存描述 |
| `common/symbols.py` | 源语言符号及 dtype/form 拼写规范化；storage 使用 Canonical 枚举 |
| `common/schema.py` | Canonical 数据类型、schema 版本及输出版本选择 |
| `cce/ir.py` | CCE parser 的私有逻辑值和语句结构 |
| `cce/normalization.py` | 合并显式 value 信息、规范指令和 operand 引用；不从名称猜 storage |
| `cce/value_versioning.py` | 规范化 AdapterProgram 的静态定义版本、alias 和循环 entry/back-edge/exit |
| `cce/affine.py` | 整数 affine 访存表达式解析 |
| `common/validator.py` | 定义、作用域、依赖、循环和访存结构校验 |
| `common/instruction_validation.py` | Catalog 指令 form、mode、签名和 operand 校验 |
| `common/diagnostics.py` | Diagnostic、ValidationResult 和 VfInfoValidationError |
| `json/adapter.py`、`json/serialization.py` | Canonical JSON 格式校验和对象序列化 |
| `programmatic/builder.py` | 显式 Canonical 对象构造 |
| `common/instruction_catalog.py` | 已知指令的语义事实来源 |

CCE 规范化只在 parser 输出时执行一次；版本化只负责生成 Canonical 定义。
CCE、JSON、直接 Canonical 和 Builder 输出共用一个 API 校验出口，每次输入接收只调用
一次完整语义校验。JSON Schema 检查属于文件格式检查，仍保留在 JSON adapter 中。

Core 信任 API 已校验的输入，不再重复调用通用 validator，只检查当前执行能力并转换表示。
直接实例化或修改 Canonical 中的 Mapping 后，应调用 `InputAPI.from_canonical()` 再交给 Core。
`InputAPI.validate_canonical_vf_info()` 则用于仅查看诊断结果，不承担输入接收与抛错。

## 输入边界

- CCE parser 只支持明确的 `__VEC_SCOPE__` 语句，不是完整 C++ 编译器。
- Catalog 确定指令语义；ISA、forwarding、II 和微架构时序由 Core 参数库处理。
- Canonical 对象合法，不代表当前 Core 支持其中全部语义；执行能力检查位于 `core/execution/prepare.py`。
- `storage_object_id` 是稳定的 UB 对象身份，`definition_id` 是一次值定义，两者不能混用。
- 寄存器 DATA 依赖使用 operand 的 `value_id` 和 producer；普通 UB 顺序由显式 Membar 表达。
- 公共诊断使用 `api.common.diagnostics.VfInfoValidationError`，不依赖 Builder。
- Canonical JSON 使用可选依赖 `jsonschema`，仅在 JSON 输入时加载。

C++ 输入和执行核心在后续阶段单独重构。`api/native/generated/` 的两份数据是
现有 Python Catalog 一致性测试的依赖，本阶段不修改这些生成数据。
