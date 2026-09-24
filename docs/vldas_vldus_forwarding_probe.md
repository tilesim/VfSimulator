# VLDAS 到 VLDUS 的无循环探针

## 测试范围

使用现有 memory_probe host 和独立 bytewise golden。CANN 9.0.0-beta.1，
dav-c310-vec，Ascend950PR_9599，关闭 VF fusion 和 vec misched。
每个 VF 只有 PSET、VLDAS、一条 VLDUS 和结果 VSTS，无循环。
对比 VLDUS 的 POST_UPDATE 与三参数不更新指针重载，以排除 VLOOP 准备。

## 实测结果

时间为 CAModel 原始绝对 cycle，done 采用 LDQ ISU_RETIRE，与原有 latency 口径一致。

| dtype/重载 | VLDAS issue/done | VLDUS issue/done | 两条 UB 请求 | golden | VF cycles |
|---|---|---|---|---|---|
| FP32 POST_UPDATE，起点4 B | 1691/1700 | 1692/1701 | 1692/1693 | 通过 | 51 |
| FP32 不更新，起点4 B | 1691/1700 | 1691/1701 | 1692/1693 | 通过 | 51 |
| FP16 不更新，起点30 B | 1690/1699 | 1690/1700 | 1691/1692 | 通过 | 51 |

POST_UPDATE 样本在 IDU 中插入了 RV_SMOV，VLDUS 因 SREG DATA hazard 延迟一拍。
不更新样本的两条指令同周期进入 LDQ，并同周期 ISU_ISSUE；VLDUS 仍被编译为
RV_VLDUI，但 offset=0。LSU 的 UB 请求先后相差一拍，并没有同周期请求。
FP32 不更新样本中，VLDAS 进入 LDU0，VLDUS 进入 LDU1。

## 结论与建模边界

- 在这些样本中，VLDAS -> VLDUS 的最小 LDQ **发射间隔为0**，不是3或1。
- UB 请求间隔为1，说明不能把发射时间与内部搬运开始时间混为一谈。
- VLDAS issue-to-done 为9；VLDUS 在 POST_UPDATE 样本为9，在同发样本为10。
  不能声称所有场景下 VLDUS issue-to-done 都固定为9。
- 采用当前单一 start 模型时，可以讨论用状态 ready=start+1、VLDUS latency=9
  近似上述内部延迟，但这是近似策略，不是实测发射 forwarding=1。
- 若要忠实表示同周期入管、下一周期内部搬运，则要单独表达 LSU 内部等待；
  不能仅填 forwarding=0 和固定 latency=9，否则会早完成一拍。

探针执行时，Core 的 `_load_ready_cycle()` 仅考虑 VF startup 和 LSQ ready，没有加载侧
align-state 依赖。现有 `bind_align_state()` 仅处理 STORE 的 append/consume 分组。
因此支持 VLDAS/VLDUS 不需要重写 IFU/IDU/LSQ 整体架构，但不能只加 Catalog/ISA：
还需补加载侧 init/use 绑定、状态初始化合法性检查及 LOAD ready 的状态门控。
按首版约定，一个 VLDAS 对应多个 VLDUS；不额外建立 VLDUS 间状态回边，
POST_UPDATE 指针依赖仍独立保留。align state 不分配 vector/predicate preg。

探针阶段只补证据；后续实现状态见下节。

## 首版实现

Python/Native 已支持 FP32、FP16、S32 的 `vldas(state, ptr)`，以及
`vldus(dst, state, ptr)` / `vldus(dst, state, ptr, inc, POST_UPDATE)`。
Catalog 分别登记 `load_init` / `load_use`，并配置以下近似：

- 两条指令都是 LOAD，进入 LSQ/LSU；latency 均为9。
- VLDAS -> VLDUS 状态门控为 VLDAS.start+1，属于上述内部等待的近似。
- 两条指令 Catalog `ub_transfer_bytes` 均暂取256，属带宽计费假设而非实测事务宽度。
- VLDUS 的 `forwarding_opcode=VLDS` 使 ParamDB 复用 VLDS 的同 form forwarding，
  不复制表；普通缺失参数仍沿用已有 fallback/warning。
- VLDAS 无普通 vector 输出、不分配 vector/predicate preg；VLDUS 分配一个 vector preg。
- 动态入队时，每条 VLDUS 捕获当前 state ID 对应的 VLDAS producer 对象。
  重新 VLDAS 只替换当前绑定，不能改变已入队 VLDUS 的依赖。
- 不额外串联 VLDUS 状态回边；POST_UPDATE 地址依赖继续独立处理。
- 首次使用时没有已执行路径上的 VLDAS（包括初始化位于零次循环）会在 Core
  入队时报错，而不是把状态当成 live-in 或无限等待。静态 validator 负责 Catalog
  签名/状态属性校验，动态初始化合法性由 Core 确认。
- Membar 按 LOAD 类别追踪两条指令；不增加 align state 容量或物理重命名限制。

测试 `tests/test_vldas_vldus.py` 覆盖三种 dtype、普通/POST_UPDATE、循环unroll、
独立状态交错、重新初始化、缺失初始化/零次循环、forwarding复用及Python/Native时序对齐。

## 复现与文件

```bash
python3 cce_code/predicate_select_test/memory_probe.py --kind vldus_straight --dtype fp32 --offset 1 --iterations 1
python3 cce_code/predicate_select_test/memory_probe.py --kind vldus_no_update --dtype fp32 --offset 1 --iterations 1
python3 cce_code/predicate_select_test/memory_probe.py --kind vldus_no_update --dtype fp16 --offset 15 --iterations 1
```

归档根目录：`results/lsu_instruction_benchmark_20260924/vldas_forwarding/`。
三个子目录按上表顺序为 `vfsim-memory-probe-z2xhl0up`、
`vfsim-memory-probe-lzvjds2k`、`vfsim-memory-probe-uiwjh839`。
均保存 kernel.cce、host.cpp、commands.json、golden/input/output、validation.json
及 core0 IDU/ISU/LSU、instr_log、instr_popped_log。
