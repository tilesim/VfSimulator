# Python 模型与 TileSim 文档

| 文档 | 内容 |
| --- | --- |
| [TileSim 对接](tilesim_integration.md) | typed program、Canonical 转换、预测与缓存 |
| [输入 API](input_api.md) | CCE、JSON、Builder 与 Canonical |
| [代码结构](code_structure_guide.md) | execution、uarch、params、trace 模块 |
| [执行模型](execution_model.md) | 组件、循环、II 和完成带宽 |
| [循环无关指令外提](loop_invariants.md) | 纯计算、标量反馈、嵌套循环边界及动态值生存期 |
| [参数配置](configuration.md) | 配置表、资源规则与文件读取 |
| [指令时序](instruction_timing.md) | latency、II、forwarding 与适用范围 |
| [A5 覆盖清单](a5_instruction_coverage.md) | 指令签名和 dtype |
| [覆盖进度](instruction_coverage/a5/progress.md) | 参数来源和未测范围 |
| [寄存器](register_model.md) | 重命名、物理资源和信用 |
| [LSU](lsu_model.md) | 访存队列、地址和共享服务资源 |
| [屏障](membar_timing_model.md) | Membar 时序与门控 |
| [验证与精度](validation.md) | 日志、周期与算子回归 |
| [TileSim 同步验证](validation/tilesim_python_sync_20261021.md) | 源码、wheel、37 组回归与下游补丁 |

安装入口见[项目说明](../README.md)，测试入口见[测试说明](../tests/README.md)。

[子 EXU 资源与时序](execution_resources.md)描述分组、forwarding/II 系数、参数来源和例外。

[子 EXU 跨分支验证](validation/sub_exu_sync_20261109.md)记录源码、独立 wheel 和纯 C++ 的同步检查。
