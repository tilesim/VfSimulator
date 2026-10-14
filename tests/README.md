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
| 组件及计算调度 | `test_component_lifecycle`、`test_ooo_dispatch`、`test_physical_register_bank`、`test_predicate_registers` |
| 访存和控制 | `test_post_update_address_state`、`test_ub_shared_issue_slots`、`test_membar_timing` |
| 指令和模式 | `test_vmov`、`test_interleave`、`test_pstu`、`test_vldas_vldus`、`test_vlds_deinterleave`、`test_packed_memory_modes` |
| 参数与输出 | `test_parameter_loading`、`test_instruction_catalog`、`test_instruction_fallback`、`test_perfetto_trace` |

`core_support.py` 提供测试环境、片段解析、动态展开、Engine 组装和日志读取。
组件测试可单独设置队列和状态；完整预测测试通过 InputAPI 接收 Canonical 输入。
参数组装使用 ParamDB.base_uarch 与 VF override。

`fixtures/` 保存固定输入，`fixtures/canonical_vf_info/README.md` 说明 Canonical 用例。
指令探针生成测试只检查源码与 golden，运行硬件测量需要对应 CANN 环境。

Native 对比统一读取 `VFSIM_NATIVE_RUNNER`。缺少 runner 的独立 Native 测试会跳过，
部分指令测试仍执行 Python 部分。
Membar fixture 测试使用 `docs/validation/membar_timing_summary.json` 的固定周期参考。
周期基线及误差回归见 [验证说明](../docs/validation.md)。
