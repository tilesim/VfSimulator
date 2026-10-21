# 自动化测试

测试通过 Python unittest 检查输入合同、执行准备、组件状态和完整预测流程。

```bash
python3 -m unittest discover -s tests -q
python3 -m unittest tests.test_ooo_dispatch
```

| 测试组 | 代表文件 |
| --- | --- |
| 输入与 Canonical 校验 | `test_input_api`、`test_cce_parser`、`test_frontend_schema`、`test_frontend_json_adapter`、`test_frontend_builder` |
| 值版本化与执行准备 | `test_value_versioning`、`test_core_lowering`、`test_core_architecture` |
| 组件及计算调度 | `test_component_lifecycle`、`test_issue_intervals`、`test_exu_completion`、`test_ooo_dispatch`、`test_physical_register_bank`、`test_predicate_registers` |
| 访存和控制 | `test_post_update_address_state`、`test_ub_shared_issue_slots`、`test_membar_timing` |
| 指令和模式 | `test_vmov`、`test_movp`、`test_predicate_logic_extensions`、`test_predicate_spill`、`test_interleave`、`test_pstu`、`test_vldas_vldus`、`test_vlds_deinterleave`、`test_packed_memory_modes` |
| 参数与输出 | `test_parameter_loading`、`test_instruction_catalog`、`test_instruction_fallback`、`test_perfetto_trace` |
| 指令覆盖清单 | `test_instruction_coverage_audit` 检查声明合并、混合类型和位宽 form 对应 |
| 向量 dtype 与参数测量 | `test_vector_dtype_extensions` 检查累加、移位、进位与 BF16 lowering；`test_vector_benchmark_analysis` 检查事件配对和参数证据条件 |
| 转换与打包 | `test_conversion_extensions` 检查混合 dtype、参数次序、显式视图和重命名依赖；`test_conversion_benchmark_analysis` 检查 lane 布局、定义区域和时序条件 |
| 归约与多结果指令 | `test_structural_vector_extensions` 检查宽化求和和分组归约；`test_structural_benchmark_analysis` 检查无借位标志、宽化数值、隐式第二结果及 carry-in 依赖 |
| 跨指令时序 | `test_cross_instruction_analysis` 检查源位置、端口、重复样本及队列隔离条件；`test_common_instruction_pairs` 对照三种 dtype 的 Python/C++ 发射周期 |
| 执行资源实验分析 | `test_fu_contention_analysis` 检查 forwarding 就绪、self-II 等待剔除、空闲端口资源冲突和归约索引的整数位校验 |

`core_support.py` 提供测试环境、片段解析、动态展开、Engine 组装和日志读取。
组件测试可单独设置队列和状态；完整预测测试通过 InputAPI 接收 Canonical 输入。
参数组装使用 ParamDB.base_uarch 与 VF override。
`test_issue_intervals` 检查穿插指令后的 self-II、端口与 form 独立性、EXQ 预测、
完成后的有效截止周期及 VEXP/VDIV 的方向转换间隔。
`test_exu_completion` 检查每 EXU 的完成周期预留、连续冲突、跨 FU 共享、
双端口独立性、端口选择、EXQ 预测与实际发射，以及重叠执行和状态释放。

固定输入统一保存在 [VFtest/](../VFtest/README.md)。CCE 用例位于 `VFtest/cce/`，
Canonical 合同和预期结果位于 `VFtest/canonical/`，JSON 文件输入位于 `VFtest/json/`。
指令探针生成测试只检查源码与 golden，运行硬件测量需要对应 CANN 环境。

Native 对比统一读取 `VFSIM_NATIVE_RUNNER`。缺少 runner 的独立 Native 测试会跳过，
部分指令测试仍执行 Python 部分。
Membar fixture 测试使用 `docs/validation/membar_timing_summary.json` 的固定周期参考。
周期基线及误差回归见 [验证说明](../docs/validation.md)。

`test_native_modes_parity.py` 对照传输延迟、信用、理论模式、EXQ 策略及 EXU 端口行为。
C++ 组件与输入测试位于 [native/tests/](../native/tests/)，通过 CTest 执行。
