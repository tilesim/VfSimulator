# VAXPY FP32 参数实测

## 已有依赖支持

`tests/test_value_versioning.py` 的 `_accumulator_vf_info()` 已覆盖
`VADD(tmp, acc) -> acc`，包括循环累加、循环出口和 unroll 后的版本化。
本次重跑该测试模块，20 项通过。

这不是已经支持 VAXPY 的证明：VADD 的旧 dst 显式出现在输入列表，
而 `vaxpy(dst, src, scalar, mask, MODE_ZEROING)` 隐式读取旧 dst。
VAXPY 接入时应将旧 dst 和 src 都转成输入值，再为 dst 创建新版本。
Catalog 现已增加 read_write，VAXPY 的第一个 operand 使用该属性；
绑定器同时生成输入和输出，版本化先读取旧值再定义新值。

## 实验环境

- CANN：9.0.0-beta.1。
- Simulator：Ascend950PR_9599；编译目标：dav-c310-vec。
- FP32，64 lane，PAT_ALL，MODE_ZEROING。
- `-O2 --cce-simd-vf-fusion=false -mllvm -cce-aicore-vec-misched=0`。
- 实际硬件日志 opcode：`RV_VAXPY`，type.F32。

## 结果

| 参数 | cycle | 依据 |
| --- | ---: | --- |
| latency | 9 | 相同指令 ID 的完成时间减 popped 时间 |
| VLDS -> VAXPY | 6 | 单条用例，两路 load 同时就绪；尚未分别隔离两种输入 |
| VAXPY -> VAXPY，旧 dst 依赖 | 5 | 16 条连续累加，相邻 start 间隔均为 5 |
| VAXPY -> VAXPY，显式 src 依赖 | 5 | 16 条 src 链，相邻 start 间隔均为 5 |
| VAXPY -> VSTS | 7 | 单条用例及旧 dst 链的最终 store |
| VAXPY self-II | 1 | 独立累加器中，同一 EXU 出现相邻周期发射 |

EXU 日志观察到 EXU0、EXU1 均执行 VAXPY，并有同周期双发。
例如独立用例两端口均在 1654、1655 执行 VAXPY。
不能把依赖链间隔 5 当成 self-II。

单条用例：load start=1646，VAXPY start=1652、done=1661，store start=1659。
旧 dst 链：VAXPY start=1654,1659,...,1729，最终 store start=1736。

四种用例均通过 FP32 逐位数值校验：单条、旧 dst 链、显式 src 链各 64 个输出，
独立链 512 个输出。独立链使用不同 scalar，避免编译器合并相同表达式。
显式 src 链为保持旧 dst 不变会产生额外复制，需按 RV_VAXPY 的 ID 提取时间，
不能将相邻日志行直接当作两条 VAXPY。

以上为已测参数，不代表所有 producer/consumer 组合已覆盖。
FP16、部分 mask、MODE_MERGING 和 GeLU 中其他指令与 VAXPY 的 forwarding/II 未测。
后续接入已将上述 FP32 参数加入 configs，并同步 Python/C++ Catalog、校验与版本化。
测试见 `tests/test_read_write_operands.py`，覆盖旧 dst、两路输入同寄存器、
循环及 unroll 的累加依赖、循环后 store 和 Python/Native 时序对齐。
MODE_MERGING 仍明确拒绝；后续需要声明按 mode 切换访问属性的规则后再开放，
不能因为支持了固定 read_write 就默认所有指令都支持 merging。

## 复现

在仓库根目录执行：

```bash
python3 cce_code/predicate_select_test/run.py --probe vaxpy_single
python3 cce_code/predicate_select_test/run.py --probe vaxpy_chain
python3 cce_code/predicate_select_test/run.py --probe vaxpy_src_chain
python3 cce_code/predicate_select_test/run.py --probe vaxpy_independent
```

脚本打印 `Artifacts` 目录，包含生成的 `kernel.cce`、编译与运行日志、
`input.bin`、`golden.bin`、`output.bin`、`validation.json` 和 simulator 日志。
脚本检查实际 RV_VAXPY 数量，防止将被优化掉的指令当成有效测量。

本次原始结果位置：

- 单条：`/tmp/vfsim-predicate-select-f8yyujgt`。
- 旧 dst 链：`/tmp/vfsim-predicate-select-s9s8ie9w`。
- 显式 src 链：`/tmp/vfsim-predicate-select-2q9987am`。
- 独立链：`/tmp/vfsim-predicate-select-8duvowzz`。

前三个基础参数主要读取 `core0.veccore0.instr_popped_log.dump` 与
`core0.veccore0.instr_log.dump`，端口及 self-II 读取
`core0.veccore0.rvec.EXU.dump`。不要混用 EXU 的内部阶段时间与 popped 时间计算 latency。
