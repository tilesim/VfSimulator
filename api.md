# VF Simulator Python 接口

输入接口及转换职责见 [api/README.md](api/README.md)，CCE 支持范围见
[api/cce/README.md](api/cce/README.md)。

```python
from api.input_api import InputAPI
from core.simulator import CoreVfCostModel

vf_info = InputAPI.load_json("tests/fixtures/canonical_vf_info/v1_valid_loop.json")
cycles = CoreVfCostModel().predict_vf_cycles(vf_info)
```

`InputAPI.load_cce()`、`InputAPI.load_json()` 和直接构造 Canonical 对象是三种
正式输入方式。程序化构造也可使用 `InputAPI.new_builder()`。

`api/` 的输出是 `CanonicalVfInfo`；`core/execution/prepare.py` 完成当前 Core 能力检查、表示转换和动态执行准备，`core/simulator.py` 组装并运行微架构模型。CCE/JSON 文件读取留在 InputAPI。预测结果中的
`vf_end_cycle` 包含启动、执行、控制节点退休和 drain。

旧 `VFInfo`、旧 JSON trace、旧转换接口及模型的 `dtype=` 兼容参数已删除。
指令精度由各自的 `form` 和 operand/value 的 `dtype` 表达；模型参数使用
`fallback_dtype`。
