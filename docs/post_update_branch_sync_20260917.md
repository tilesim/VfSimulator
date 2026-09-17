# POST_UPDATE 跨分支同步记录

日期：2026-09-17。来源提交：master `6cf612c`。

## 同步范围

仅同步以下三项，不合并分支上的其他实验：

- POST_UPDATE 地址状态的 RAW/WAR 发射依赖、配置、日志和测试。
- 内部默认精度明确命名为 fallback_dtype / fallbackDtype，不改变指令自身 dtype/form。
- 删除静态逻辑寄存器数量与物理寄存器容量比较的无效 warning。架构寄存器预警留待后续。

| 分支 | 同步提交 | Python unittest | Native CTest |
| --- | --- | --- | --- |
| vfinfo-core-api-unification | 22fdaae | 217 通过 | 7/7，使用同源 Native 构建 |
| cpp-native-vfsim | 480ede6 | 不适用，纯 C++ | 7/7 |
| MultiSoC | 6adc45a | 209 通过 | 6/6 |
| MultiSoC-membar | 1d7c738 | 246 通过 | 6/6 |
| ub-address-dependency-experiment | 244b68d | 238 通过 | 7/7 |
| shq-exq-rr-alu-sfu-python | 85cb39d | 201 通过 | 6/6 |
| exq-improvement-experiment | 19abdf5 | 210 通过 | 6/6 |
| shq-inorder-experiment | f7fd889 | 226 通过 | 5/6，见下文历史失败 |

Python 测试配置了对应 Native runner，包含动态开始/完成周期及地址依赖日志对照。
历史分支的 Native JSON 主 runner 仍读旧格式，因此 UB 分支使用
`vfsim_membar_canonical_test_runner`，三发射分支使用新增的
`vfsim_address_canonical_test_runner`。

## 保留的分支差异

- MultiSoC 两分支继续使用 SoC 固定配置，新字段写入 DV100/DV121；未恢复 uarch override schema。
- UB 两分支保留其字节区间、独立指针、POST_UPDATE 地址推进与局部阻塞逻辑；地址状态发射约束不替代 UB 数据依赖。
- RR、统一 EXQ、三发射实验策略未改动。
- 较早 UB/三发射分支保留现有 VFInfo 兼容结构，不为本次同步迁移整个前端。
- 旧本地 mem-bar-local-dep-mvp worktree 保持 `405dd9c` 历史快照，未修改。
- 本次只更新本地分支，没有 push。

## 已知限制与验证边界

三发射分支的 Native smoke 报错：
`balanced reserve must return flexible work to EXQ0 once the target gap is reached`。
已对迁移前 `82081d8` 独立构建并复现完全相同的失败，没有修改调度策略或测试来掩盖它。
新增地址状态测试通过。

MultiSoC-membar 原有定时 Membar 实验仅在 Python 侧，Native 仍为旧全局 barrier。
带 Membar 的混合 POST_UPDATE case 为 Python 78、Native 65 cycles，因此该分支的
新增跨语言测试只对无 Membar 的地址状态时序进行逐项比较，不声称两侧 Membar 等价。
较早 UB/三发射分支 Native 尚未支持 align-state，未将 VSTUS/VSTAS 纳入其跨语言测试。

UB、MultiSoC-membar、三发射还运行了 `tools/run_post_update_validation.py`：
显式 offset 和 POST_UPDATE 各 U1/U2/U4/U8 共八组，Python/Native 逐项一致。
没有地址更新的四组均为 189/194/198/189 cycles，开关地址状态不改变结果。
UB 与 MultiSoC-membar 的 POST_UPDATE 为 189/190/190/187；三发射实验分支为
189/189/189/187。差异来自各分支原有调度，不能套用 master 的统一基线。

本次跨分支验证以全部单测、Native CTest 和上述专项为主，未对每个历史分支
重跑完整精度回归集。来源 master 已完成 25 个 full regression case 的迁移前后
严格周期一致性比较。POST_UPDATE 的硬件 IDU/SREG stall 尚未建模，详见开发计划。
