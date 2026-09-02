# 统一 EXQ 穿刺实验计划

## 1. 目标

当前后端在 SHQ 阶段就把 ready 计算指令绑定到 `EXQ0` 或
`EXQ1`，后续只能进入对应的 `EXU0` 或 `EXU1`。由于部分指令仅能在
`EXU0` 执行，这种过早绑定可能造成两套后端负载不均。

本实验将两条 EXQ 合并为一条统一 EXQ，把端口选择推迟到
EXQ 向 EXU 实际发射的周期。第一阶段只修改 Python 模型，保留旧双
EXQ 路径作为 A/B 基线。

## 2. 队列和容量

新路径为：

```text
IDU -> OoO/SHQ -> Unified EXQ -> EXU0/EXU1
```

- SHQ 仅将已 ready 的指令送入统一 EXQ。
- SHQ 到 EXQ 按 `stream_seq/inst_id` 的 oldest-ready 顺序选择。
- 该阶段不再做端口 RR、`EXU0_ONLY` 压力预留或 `exu_port` 绑定。
- 统一 EXQ 容量默认为 `52`，等于旧模型两条深度为 `26` 的
  EXQ 容量之和。
- SHQ 每周期最多送入 `2` 条指令，与旧模型每个 EXQ 接收
  `1` 条的总带宽一致。

## 3. EXQ 向 EXU 的有限窗口乱序

每周期只检查统一 EXQ 的前 `N=8` 个物理位置。窗口不是“前 8
条可发射指令”，第 9 条及之后的指令即使可发射也不参与当周期
仲裁。

按窗口内顺序扫描指令：

1. 根据 `dispatch_exu` 生成合法端口：`EXU0_ONLY -> {0}`，
   flexible 指令 `-> {0, 1}`。
2. 对每个合法 EXU，使用该 EXU 上实际上一条发射指令检查：

   ```text
   current_cycle >= last_issue_cycle[exu] + II(last_op[exu], current_op)
   ```

3. 指令在两个 EXU 上都不满足 II 时，留在 EXQ，继续检查下一条。
4. flexible 指令的端口选择顺序为：
   - 最早合法发射周期更小的 EXU；
   - 两边相同时，选择当前 inflight 数量更少的 EXU；
   - inflight 也相同时，优先 `EXU1`。
5. `EXU0_ONLY` 指令仅能选择 `EXU0`，不参与 inflight 平衡。只要
   EXU0 当前满足 II 和 inflight cap 就可以发射。保留可选的
   `unified_exq_skip_exu0_only_when_imbalanced` 穿刺开关用于复现历史
   对照，默认关闭。
6. 每个 EXU 每周期最多发射一条。
7. 前 8 条全部不可发射时，当周期零发射，下周期重新判断。

指令被绕过后，若后续指令在某个 EXU 实际发射，该 EXU 的
`last_op/last_issue_cycle` 立即更新。下周期对原指令的 II 必须基于新的
实际发射前驱计算。

## 4. 不在本次实验范围内的内容

- 不新增未来写回周期 reservation。寄存器写口冲突继续由 II 表及
  现有 II fallback 规则保证。
- 不修改 latency、forwarding、II 配置。
- 不修改 LSU、Membar、物理寄存器释放或 IDU 信用模型。
- 不同步 C++；等 Python 穿刺结果确认后再决定是否迁移。

## 5. 配置

```json
{
  "enable_unified_exq": true,
  "unified_exq_depth": 52,
  "shq_to_unified_exq_width": 2,
  "unified_exq_issue_window": 8,
  "unified_exq_skip_exu0_only_when_imbalanced": false
}
```

## 6. 验收用例

1. SHQ 中 ready 指令按 oldest-ready 进入统一 EXQ，不绑定 EXU。
2. 队首指令在两个 EXU 上均因 II 阻塞时，窗口内后续指令可绕过。
3. 前 8 条全部阻塞时，第 9 条不能发射。
4. `II=2` 且上一条指令在 cycle 10 发射时，当前指令 cycle 11
   阻塞、cycle 12 可发射。
5. flexible 指令在两边 II 和 inflight 相同时选择 EXU1。
6. flexible 指令选择 inflight 更少的 EXU。
7. 默认策略下 `EXU0_ONLY` 不因 inflight 不平衡被跳过；显式开启
   穿刺开关时，仍可复现该对照行为。
8. 关闭开关后旧双 EXQ 行为和现有回归保持不变。
