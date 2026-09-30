# VMOV 指令覆盖与 A5 实测

## 输入合同

`vmov(dst, src)` 对应底层 `RV_VMOV Vd, Vn`，复制整个向量寄存器。
它是无 mask 的普通计算指令，不是 VDUP 广播，也不是零成本的 CCE `dst = src` alias。
Canonical 中是一个 Register SOURCE 输入、一个 Register DESTINATION 输出，
进入正常 SSA、RAT、物理向量寄存器分配及 last-use 释放路径。
本次登记 `fp32` 和 `b32`，对应本次 FP32 探针的同一整寄存器复制语义；不宣称其他类型已实测。
Python/C++ 共用 Catalog、参数和生成的 C++ 表，不增加调度器特判。

## 参数

| 项目 | cycle / 配置 | 证据 |
|---|---|---|
| latency | 6 | popped 到 instr_log 完成差值 |
| 执行单元 | ALU / EXU01 | EXU.dump 中两端口均执行，可每 cycle 双发 |
| forwarding(VDUP, VMOV) | 2 | 单条及原 GeLU_grad 日志 |
| forwarding(VLDS, VMOV) | 6 | FP32 NORM load 独立探针 |
| forwarding(VMOV, VMOV) | 2 | 16 条依赖链 |
| forwarding(VMOV, VSTS) | 4 | 单条及依赖链末尾 store |
| II(VMOV, VMOV) | 1 | 8 条不同值独立链，同端口相邻发射 |

其他 forwarding/混合 II 仍走现有 fallback 和 warning；没有据此修改 VDUP 到其他计算的参数。

## 可复现探针

```bash
python3 cce_code/predicate_select_test/run.py --probe vmov_single
python3 cce_code/predicate_select_test/run.py --probe vmov_chain
python3 cce_code/predicate_select_test/run.py --probe vmov_independent
python3 cce_code/predicate_select_test/run.py --probe vmov_load
```

使用 CANN 9.0.0-beta.1、`dav-c310-vec`、`-O2`、misched=0。
脚本输出目录包含 kernel.cce、host.cpp、validation.json（含命令）、二进制输入/输出/golden，
以及 core0 的 instr_log、instr_popped_log、EXU.dump。校验是 FP32 逐字节比较。
脚本断言 VMOV 数量为 1/16/32，避免编译器消除复制后仍宣称获得 II。

本次原始记录：

| 探针 | 目录 | 关键记录 |
|---|---|---|
| 单条 | `/tmp/vfsim-predicate-select-jj_7utk9` | VDUP start=1649；VMOV start=1651、done=1657；VSTS start=1655 |
| 依赖链 | `/tmp/vfsim-predicate-select-ns11j7o8` | VMOV start=1651,1653,...,1681；VSTS start=1685 |
| 独立链 | `/tmp/vfsim-predicate-select-4vk13jrk` | 两端口 VMOV 从 popped=1652 起连续双发，EXU 日志周期比 popped 大 1 |
| load 输入 | `/tmp/vfsim-predicate-select-45thv2vc` | VLDS start=1646；VMOV start=1652；VSTS start=1656；64 个 FP32 元素逐字节校验通过 |

Load 输入参数登记为 `VLDS.fp32 -> VMOV.fp32/b32 = 6`，由 Python/Native 共用。
本次未独立测量 FP16、其他 load mode 到 VMOV 的时序，不扩展为这些模式的实测结论。

先前同源独立复制探针 `/tmp/vfsim-predicate-select-c_4g66wi` 被合并成一条 VMOV，
不作为 self-II 证据。当前生成器改为 8 个不同值、每条原地复制 4 次。
临时原始目录不保证跨机器存在；上述命令及源码纳入版本管理，可重新生成。

用户提供的 GeLU_grad baseline/u1 日志也支持这些结论：
`results/ub_address_dependency_experiment/GeLU_grad/experiments/baseline/u1/logs/camodel/`。
其中 VDUPS ID74/75 在 2639 发射，VMOV ID84/88 在 2641 发射、2647 完成。
原 CCE 中循环内的常量 VDUP 被编译器外提并在循环内产生 VMOV；
本次只支持显式 VMOV，不在 CCE 前端模拟该编译器优化，也不把所有 alias 强制改为 VMOV。
