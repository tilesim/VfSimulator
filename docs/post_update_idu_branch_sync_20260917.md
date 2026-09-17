# POST_UPDATE IDU 修订跨分支同步

日期：2026-09-17。来源提交：master `a4d355f`。
本文替代首版 LSU 实现的同步结论，不覆盖其历史记录。

## 分支与提交

| 分支 | 当前提交 | Python unittest | Native CTest |
| --- | --- | --- | --- |
| vfinfo-core-api-unification | 64ad649 | 220/220 | 7/7 |
| cpp-native-vfsim | a7f5fa2 | 纯 C++ | 7/7 |
| MultiSoC | dab7451 | 212/212 | 6/6 |
| MultiSoC-membar | 3f20b43 | 249/249 | 6/6 |
| ub-address-dependency-experiment | 3c02e0b | 241/241 | 7/7 |
| shq-exq-rr-alu-sfu-python | 1eceaab | 204/204 | 6/6 |
| exq-improvement-experiment | 3e7f8a4 | 213/213 | 6/6 |
| shq-inorder-experiment | 3082a53 | 229/229 | 5/6 |

vfinfo-core-api-unification 的代码移植提交为 `deded39`，`64ad649` 补充该分支验证说明。
其余七个分支的表中提交同时包含代码和验证说明。
所有 Python 测试均启用本分支 Native runner，没有因缺少 runner 跳过地址时序对照。
纯 C++ 分支额外使用正式 API 分支的测试驱动，通过 19 项地址专项测试。

## 本次同步内容

- 从 OoO/LSU 移除首版地址事件绑定与 LSU start 间隔限制。
- IDU 顺序 dispatch 时维护地址 scoreboard；更新使地址在 dispatch + latency 就绪。
- 默认 `idu_post_update_ready_latency=1`，队头地址未就绪时后续指令不得绕过。
- 旧 `lsu_post_update_ready_latency` 明确报迁移错误。MultiSoC 分别更新 DV100/DV121
  固定配置，不恢复已删除的 uarch override schema。
- 成功日志 `idu_to_ooo.json` 增加 dispatch-based 地址依赖；新增
  `idu_address_blocked.json`，不再在 sim_history 输出旧 LSU 地址事件。
- 保留每个分支的 UB 区间/指针数值推进、局部阻塞、SoC 展开和 SHQ/EXQ 策略。
- 之前的 fallback_dtype 命名与无效 vreg warning 清理已在各分支，无需重复移植。

## 关键实验

AABBCC 的显式 offset 与 POST_UPDATE 各 U1/U2/U4/U8，共八组，两端逐项一致：

| 配置/分支 | 显式 offset U1/U2/U4/U8 | POST_UPDATE U1/U2/U4/U8 |
| --- | --- | --- |
| A5 普通分支（含两个 MultiSoC 的 DV100） | 189/194/198/189 | 189/186/276/290 |
| 三发射实验分支当前配置 | 189/194/198/189 | 189/185/276/290 |
| 两个 MultiSoC 的 DV121 | 145/147/145/146 | 145/177/267/281 |

积压 probe 的同指针 load 在 IDU cycle 26..41 逐周期通过，记录 13 次地址阻塞；
独立指针及显式 offset 都无地址阻塞。三种模式的后端均连续 8 cycle 双发 16 load。
正式 API、纯 C++、旧 UB 分支为 159 cycles；MultiSoC、RR、EXQ、三发射分支
为 157 cycles，来自保留的旧全局 Membar 语义；MultiSoC-membar Python 为 159。
后者由于原有 Python/Native Membar 能力不同，只对无 Membar 用例做完整时序对照，
含 Membar 的积压 probe 本轮仅验证 Python，没有宣称两侧 barrier 等价。

## 回归与限制

- 正式 API Python full regression 25 个 case 与 master
  `baseline_canonical_membar.json` 的 vf_end 严格相等。
- 纯 C++ Native full regression 25 个 case 与同一基线严格相等，也与上述 Python 相等。
- Python 回归脚本默认仍选该分支的旧 RR 基线（有三个已知历史差值）；已额外读取最新
  master 基线逐项比较，结果无差值。本次未改任何精度基线。
- 各历史实验分支运行全部单测、CTest 和专项，未逐分支重跑整套精度回归。
- 三发射 Native smoke 仍有已知失败：
  `balanced reserve must return flexible work to EXQ0 once the target gap is reached`。
  上一轮已在迁移前 `82081d8` 独立构建复现。本轮其余五项、地址专项及两端对照通过，
  不修改策略或断言掩盖该问题。
- 旧 UB/三发射 Native 尚无 align-state lowering，仍不将 VSTUS/VSTAS 列为对照覆盖。
- compileall、所有提交的 diff --check 通过。

各目标分支均有 `docs/post_update_idu_sync.md`，记录本分支复现步骤与限制。
本机测试输出位于 `/tmp/vfsim-idu-sync-*-validation`、`*-backlog`、`*-a6`，
完整回归为 `/tmp/vfsim-idu-sync-vfinfo-regression-python` 和
`/tmp/vfsim-idu-sync-native-regression`。这些临时日志不提交，脚本和汇总已入库。

本次只更新本地分支，没有 push。主工作区保持 master，原 build-native 未跟踪目录
不纳入提交。旧 mem-bar-local-dep-mvp worktree 保持 `405dd9c` 历史快照，不做同步。
