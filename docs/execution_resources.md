# 子 EXU 资源与时序

每个物理 EXU 包含独立维护接收时间的计算资源。`isa.json` 的 form 字段
`execution_resource` 声明资源归属，`dispatch_exu` 声明可用物理端口。
Python 和 C++ 使用同一套配置、参数查询规则和发射约束。
规则与校验位于 `core/params/resources.py`、`native/core/params/resources.h/.cpp`；
子单元状态分别位于 `core/uarch/sub_exu.py`、`native/core/uarch/sub_exu.h`。

## 资源分组

| 资源 | 已分组 form 数 | 示例 |
| --- | ---: | --- |
| ADD_DUAL | 50 | VADD、VSUB、VADDS、VCI 的已确认类型 |
| ADD_CONV | 57 | 已确认的 VCVT 转换及相关操作 |
| MUL | 27 | VMUL、VMULS、VMADD、VAXPY 的已确认类型 |
| MOV | 11 | VMOV 的已确认类型 |
| LNEXP | 8 | VEXP、VDIV、VLN、VSQRT 的 FP16/FP32 |

完整 form 声明以 [isa.json](../configs/isa.json) 为准。这 153 个分组基于 A5
指令时序与竞争实验，表达仿真使用的资源归属。同名资源内的 FP16/FP32 共享接收状态。
其余 form 保留显式参数查询、按 opcode/form 的 self-II 与缺失参数诊断。
资源归属和跨指令估计的适用范围分别记录，参数估计的使用范围包含尚未逐对实测的组合。

## 发射约束

在周期 `t` 接收一条指令 A 时，其资源 R 更新为：

```text
ready(EXU, R) = max(ready(EXU, R), t + self_II(A))
```

候选指令 B 的最早接收时间同时受以下条件限制：

- 所属子单元的 `ready`。
- 物理 EXU 最近一次接收的下一周期。
- 例外表中先前 producer 对 B 建立的截止周期。
- 操作数就绪、队列接收延迟和 inflight 容量。
- 向量、谓词目标 bank 的完成周期名额。

例如 VCI.int32 在 100 接收且 self-II=2，ADD_DUAL 到 102 可再接收；
MUL 或 LNEXP 可以在 101 接收。中间的独立指令保留 ADD_DUAL 原有截止周期。
VEXP.fp32 在 100 接收后，LNEXP 到 104 开放；VEXP→VDIV 的例外 II=6
使 VDIV 到 106 才可接收。其他子单元的指令和 producer 完成都保留该例外的截止周期。
两个物理 EXU 分别维护所有这些状态。

EXQ 按资源分队列，所有资源队列共享同一物理端口的 `exq_depth`。
调度从可接收的各队首中按操作数就绪、接收周期和动态 ID 选择。
端口预测考虑已发射资源状态、同资源队尾、待发射例外 producer 和已预留的完成周期；
实际发射时再次查询当前状态。

默认每个 EXU 最多在执行 7 条计算指令；每周期接收一条指令。
同一周期最多完成一条写向量寄存器的指令和一条写谓词寄存器的指令。
一个资源的接收间隔可以小于指令 latency，多条指令因此可以重叠执行。

## 参数查询

显式参数和资源公式分别承担已配置指令对与可推导组合的时序查询。
查询顺序为 `timing_overrides.json`、显式参数表、分组公式、缺失回退。
forwarding 的 Catalog opcode 继承和 producer `*` 规则继续参与显式查询。

交叉 II 的资源公式为：

```text
gap(A, B) = self_II(A)    两者同组
gap(A, B) = 1             两者异组
```

若两者写同类寄存器，且 `latency(A) == gap + latency(B)`，静态 II 查询返回
`gap+1`。例如 VMUL.fp32 latency=8、VADD.fp32 latency=7，基础间隔为 1，
写回冲突使查询 II=2。实际发射通过完成周期预留检查所有在执行指令的冲突。

forwarding 的资源公式由 [execution_resources.json](../configs/execution_resources.json) 定义：

```text
fwd(A, B) = max(2, latency(A) - offset)
offset = 3    任一端属于 LNEXP
offset = 4    其余已分组组合
```

该规则表达已观测的经验关系；每个推导值保留估计属性。依赖可用时间与单元接收间隔
分别建模，self-II 不直接决定 forwarding。

## 例外和证据

[timing_overrides.json](../configs/timing_overrides.json) 使用完整 `OP.form` 有向键：

```json
{
  "schema_version": 1,
  "ii": {"VEXP.fp32": {"VDIV.fp32": 6}},
  "forwarding": {"VMULL.int32": {"VMULL.int32": 5}}
}
```

当前有 21 项 II、19 项 forwarding 例外。II 例外中 19 项涉及 LNEXP，
另两项为转换到 VABS.int32。例外数据包含实测记录与已有配置中保留的值；
来源未完整归档的既有值继续作为待验证参数。self-II 统一存放在
`InitiationInterval.json`，例外表的 II 只接收不同 COMPUTE form 之间的约束。

[参数证据清单](data/sub_exu_timing_migration_20261109.json) 保存来源提交、文件摘要、
753 项配置查询值和另 9 项混合 dtype 实测 II。Python 与 C++ 测试逐项检查这些值，
并覆盖共享资源、空闲资源穿插、方向例外、EXQ 预测和寄存器完成 bank。

benchmark 工具对符合公式的结果保留测量证据；偏离公式的结果写入例外表。
已有显式值发生变化时，通过工具报告的精确 `--replace` 参数路径确认校准。
尚未分组的指令对及访存指令对继续写入对应显式参数表。

## 算子回归

37 个用例及另 11 个默认输入的 Python/C++ 预测一致。有 CAModel 参考的 33 个用例
MAPE 为 3.85%；GeLU、SwiGLU、Softmax 的分组平均误差减小，SiLU 的分组平均误差增大。
逐用例周期和对照口径见[算子回归](validation/sub_exu_timing_20261109.md)。
