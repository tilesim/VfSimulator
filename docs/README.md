# 项目文档

| 文档 | 内容 |
| --- | --- |
| [代码结构](code_structure_guide.md) | C++ 模块、组件和周期推进 |
| [输入 API](input_api.md) | CCE、Canonical JSON 和 Builder |
| [执行模型](execution_model.md) | 组件流转、循环、II 与完成带宽 |
| [循环无关指令外提](loop_invariants.md) | 纯计算、标量反馈、嵌套循环边界及动态值生存期 |
| [参数配置](configuration.md) | 参数文件、加载快照和查询回退 |
| [指令时序](instruction_timing.md) | latency、II、forwarding 与实验边界 |
| [A5 指令覆盖](a5_instruction_coverage.md) | CANN 接口与 dtype 范围 |
| [覆盖进度](instruction_coverage/a5/progress.md) | 实测来源、同编码继承和缺口 |
| [算子指令对](instruction_coverage/a5/operator_pairs.md) | VFtest 的 II 与 forwarding 需求 |
| [归档算子指令对](instruction_coverage/a5/membar_pairs.md) | Membar 归档的参数覆盖 |
| [寄存器模型](register_model.md) | 动态定义、物理资源和信用 |
| [LSU 模型](lsu_model.md) | 访存队列、依赖及服务资源 |
| [屏障时序](membar_timing_model.md) | Membar 门控和释放 |
| [C++ 一致性验证](validation/cpp_refactor_sync_20261021.md) | 源码映射、共享配置与 37 组逐指令回归 |

构建与运行见[项目说明](../README.md)，测试入口见[测试说明](../tests/README.md)。

[子 EXU 资源与时序](execution_resources.md)描述分组、forwarding/II 系数、参数来源和例外。

[子 EXU 跨分支验证](validation/sub_exu_sync_20261109.md)记录源码、独立 wheel 和纯 C++ 的同步检查。
