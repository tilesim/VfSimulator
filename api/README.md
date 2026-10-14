# Python 输入 API

`api/input_api.py::InputAPI` 接收 CCE/DSL 文件、Canonical JSON 和直接构造的
`CanonicalVfInfo` 对象。API 输出通过语义校验的 Canonical 程序，供 Core 执行准备使用。

## 入口

| 方法 | 用途 |
| --- | --- |
| `load_cce(path, kernel_name=None, loop_params=None)` | 读取所选 VF scope，解析、版本化并校验 |
| `load_json(path)` | 检查 JSON Schema，解码并校验 Canonical |
| `from_canonical(vf_info)` | 校验直接构造的对象并返回原对象 |
| `new_builder(params=None, uarch=None, source=None)` | 创建显式构造工具，参数以关键字传入 |
| `validate_canonical_vf_info(vf_info)` | 返回结构化诊断结果 |

`VfInfoBuilder.build()` 返回校验成功的 Canonical 对象。
直接对象及其 Mapping 修改通过 `from_canonical()` 接收。
正式接收出口执行一次通用语义校验，Core 负责执行能力检查和运行期约束。

## 目录职责

| 目录或文件 | 内容 |
| --- | --- |
| `input_api.py` | 组合输入方式，提供公开接收接口 |
| `cce/` | 源码、私有 IR、Catalog 绑定、规范化、affine 解析和值版本化 |
| `json/` | Canonical JSON Schema、文件检查和对象编解码 |
| `programmatic/` | Builder、循环构造和显式定义管理 |
| `common/` | Canonical 类型、Catalog、符号规范化、诊断和语义校验 |

`common/schema.py` 定义 value、operand、storage object、memory access、instruction、
loop、membar 及 source location。存储类别通过显式声明识别。
`common/instruction_catalog.py` 从 `configs/instruction_catalog.json` 读取指令语义。
JSON 的 schema v1/v2 由 `json/serialization.py` 处理。

`api/native/generated/` 保存从共享 Catalog 和 override schema 生成的 C++ 数据表，
生成脚本位于 `tools/`，一致性由 `tests/test_instruction_catalog.py` 检查。

调用示例见 [Python 接口](../api.md)，字段及转换流程见 [输入 API 说明](../docs/input_api.md)，
CCE 语法范围见 [CCE 前端](cce/README.md)。
