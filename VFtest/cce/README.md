# CCE 测试输入

本目录统一保存 `.cce` 测试源码，供 VFSim 输入、指令覆盖、参数实验和回归验证使用。

- 根目录保存 GeLU、SiLU、SwiGLU、online update 等算子和独立探针。
- `consumer_done/` 保存与回归参考数据对应的时序样本。
- `contracts/` 保存 CCE 输入合同用例，预期 Canonical JSON 位于 `../canonical/cce_expected/`。
- `membar_timing/` 保存屏障时序用例；`ordinary_instructions/` 保存指令覆盖用例。
- `softmax/` 保存四组 softmax 指令组合；相关运行证据位于 `regression_suite/evidence/cce/softmax/`。

回归清单位于 `regression_suite/cases/cost_model_regression_cases.json`，
以 `cce_vf_end` 和 `cce_vf_end_source` 记录 CAModel 参考周期及来源。
