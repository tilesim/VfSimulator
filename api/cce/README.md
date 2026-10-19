# CCE 前端

CCE 前端读取包含 `__VEC_SCOPE__` 的 CCE/DSL，生成通过校验的 `CanonicalVfInfo`。

## 调用

```bash
python3 main.py --cce VFtest/cce/GeLU_poly.cce --out_dir results/gelu
```

```python
from api.input_api import InputAPI
from core.simulator import CoreVfCostModel

vf_info = InputAPI.load_cce("VFtest/cce/GeLU_poly.cce")
cycles = CoreVfCostModel(out_dir=None).predict_vf_cycles(vf_info)
print(cycles)
```

一个文件有多个 VF 函数时，CLI 使用 `--cce-kernel NAME`，Python 使用
`kernel_name=NAME`。`api.cce.adapter.list_cce_vf_kernels(path)` 提供函数列表。
`loop_params` 提供循环次数等整数参数。

## 转换流程

`adapter.py` 组合源码解析、值版本化和 Canonical 校验。
`parser.py` 选择 scope、解析语句并按 Catalog 绑定 operand、form 和指令类别，
生成经过 normalization 的 `AdapterProgram`。
`value_versioning.py::ValueVersioningPass` 建立 definition、producer、alias 和循环
entry/back-edge/exit，随后由公共 validator 检查程序语义。

Core 的 `core/execution/prepare.py::CoreLoweringPass` 接收校验成功的程序，执行能力
检查和表示转换；同文件的 `prepare_program()` 组合静态布局及动态展开。
完整调用关系见 [输入 API](../../docs/input_api.md)。

## 语法和地址描述

- 解析 vector 指令、loop、Membar 和已登记的无时序语句。
- 已知指令按 `configs/instruction_catalog.json` 声明绑定签名、form、mode 和 call variant。
- 未知向量 opcode 可携带明确的 COMPUTE 语义进入时序回退。
- vector、predicate、scalar 和 UB pointer 使用词法作用域。
- `dst=src` 表达 value binding；显式 VMOV 表达向量复制指令。
- UB alias 保留对象身份，普通 offset 使用内存元素单位。
- POST_UPDATE 根据指针 dtype 和 Catalog 编码产生字节增量，并声明地址状态。
- VSSTB 的复合配置以低 16 位乘 32 B 计算指针更新量。
- affine offset 支持常量、合法循环变量、加减和常量乘法。
- 不在支持范围内的 scope 语句触发解析错误。

## 屏障

`mem_bar(VLD_VST)` 和 `mem_bar(VST_VLD)` 生成 CanonicalMembar 节点。
控制器根据前序指定类别访存的完成进度和配置时序放行后续访问。
Membar 的 issue、release、retire 和 consumer gate 规则见
[Membar 时序](../../docs/membar_timing_model.md)。
