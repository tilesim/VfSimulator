# MOVP 与 VMOV 输入转发参数

`MOVP_B16`、`MOVP_B32` 从 VF 外设置的 MASK 特殊寄存器读取掩码，
产生 bool 类型的 PredicateRegister 输出。签名包含一个目的操作数；CCE 支持
`vector_bool p=movp_b32()`、`p=movp_b32()` 和 `movp_b32(p)`。
消费者通过该输出的 SSA 定义建立依赖，资源使用谓词物理池。

## MOVP 测量参数

来源：`vfinfo-core-api-unification` 提交 `4858c91`。
环境：2026-10-16/18，CANN 9.0.0-beta.1、dav-c310-vec、Ascend950PR_9599 CAModel，O2、misched=0。

| 指令 | latency | FU / 端口 | self-II | VDUP / VSEL forwarding | PSTS.b8 forwarding |
| --- | ---: | --- | ---: | ---: | ---: |
| MOVP_B16.b16 | 6 | ALU / EXU01 | 1 | FP16：2 | 4 |
| MOVP_B32.b32 | 6 | ALU / EXU01 | 1 | FP32：2 | 4 |

全零、全一、稀疏 mask、B16 半区和 B32 低半区探针同时校验数值与保护区。
consumer forwarding 在向量源提前就绪后测量。VF 外 MASK 设置耗时超出该指令的建模范围。

编译器会合并重复 MOVP 调用。self-II 证据使用保留 4/6 个独立 predicate 结果的控制版，
依据真实编码仅将目标指令替换为同 Pd 的 MOVP，保持 PC、ELF 长度和其余指令不变。
控制版及目标版分别通过 golden，并核对无 spill、实际 opcode 和端口。
同一 EXU 观察到相邻周期发射，两个端口也出现同周期发射。未测消费者及交叉 II 保留回退和 warning。
原始采样位于 `results/a5_movp_parameters_20261016` 和 `results/a5_movp_ii_parameters_20261018`，
本地结果目录按仓库约定保存。

自动测试为 `tests/test_movp.py`，CCE 合同案例位于 `VFtest/cce/contracts/movp.cce`。

## VMOV → VAXPY

来源：提交 `792be98`。`VMOV.fp32/b32 → VAXPY.fp32` 的 forwarding 均为 2。
旧目的输入和显式源输入分别通过 8 条 VMOV 依赖链隔离，按动态指令身份关联源物理寄存器。
探针为 `run.py --probe vmov_axpy_dst` 和 `run.py --probe vmov_axpy_src`，
自动测试位于 `tests/test_vmov.py`。
