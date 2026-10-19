# 谓词保存与恢复的参数证据

`psts(predicate, pointer, byte_offset, NORM)` 保存完整的 256-bit 谓词位图；
`plds(predicate, pointer, byte_offset, NORM)` 读取该位图并产生一个新的 PredicateRegister 定义。
固定 form 为 `b8`，有效载荷 32 B，Canonical 声明 `address_unit_bytes=1`、`span=32`。

指针 alias 按 C 指针元素宽度换算。对于 uint32 指针 `slot=base+8`，
`psts(p,slot,32,NORM)` 相对 base 的偏移为 64 B。当前签名接受 NORM。
内存同步由 Membar 控制，谓词依赖通过 SSA 和寄存器资源建立。

## 测量参数

来源：`vfinfo-core-api-unification` 提交 `2bcefb1`。
环境：2026-09-30，CANN 9.0.0-beta.1、dav-c310-vec、Ascend950PR_9599 CAModel，misched=0。

| 参数 | cycles | 覆盖 |
| --- | ---: | --- |
| PLDS / PSTS latency | 9 | 独立 issue→done 样本 |
| PSET_B8/B16/B32 → PSTS.b8 | 4 | 三种位宽的完整位图和写入区间 guard |
| VCMP_EQ.fp32 → PSTS.b8 | 4 | 比较结果保存 |
| PLDS.b8 → VSEL.fp32 / VADDS.fp32 / PAND.b8 / VCMP_EQ.fp32 | 6 | 非全真掩码与对应消费者 |
| PLDS.b8 → PSTS.b8 / VSTS.fp32 | 8 | 谓词复制与掩码 STORE |

UB 调度预算使用每条 256 B 的近似。小事务吞吐、混合端口约束及未列出的消费者仍需补测。
存在竞争时，issue→done 可包含额外等待；参数确认使用隔离样本。

## 复现

在仓库根目录执行：

```bash
python3 skills/vfsim-instruction-benchmark/scripts/memory_probe.py --kind predicate_compare_store --iterations 1
python3 skills/vfsim-instruction-benchmark/scripts/memory_probe.py --kind predicate_pset_store16 --iterations 1
python3 skills/vfsim-instruction-benchmark/scripts/memory_probe.py --kind predicate_pset_store32 --iterations 1
python3 skills/vfsim-instruction-benchmark/scripts/memory_probe.py --kind predicate_load_store --iterations 1
python3 skills/vfsim-instruction-benchmark/scripts/memory_probe.py --kind predicate_load_select --iterations 1
python3 skills/vfsim-instruction-benchmark/scripts/memory_probe.py --kind predicate_load_masked_store --iterations 1
```

其余 `predicate_load_compute`、`predicate_load_logic`、`predicate_load_compare` 和
`predicate_pset_store` 分别覆盖计算、逻辑、比较消费者及 B8 保存。
原始采样记录中的位图保存、恢复、guard 与掩码消费均通过独立逐字节 golden 校验。
每次运行输出 `Artifacts:`，保存源码、编译命令、输入/golden/输出和指令日志。
自动测试为 `tests/test_predicate_spill.py`，固定合同案例位于 `VFtest/cce/contracts/predicate_spill.cce`。
