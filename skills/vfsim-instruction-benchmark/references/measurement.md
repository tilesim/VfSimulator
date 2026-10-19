# 实验方法与配置示例

## 日志事件与样本口径

常见指令事件包含 `[cycle] ... (ID: id) RV_OP ...`。解析器应保留运行、core、veccore 和 VF 调用身份，再按动态 ID 配对 popped 与完成日志。缺失事件、重复 ID 和 opcode 不一致的样本单独记录。日志格式以实际 CANN 输出为准。

为每个样本保存：`dynamic_id`、opcode/form、issue、done、EXU 端口、源/目的寄存器、谓词、日志文件及行号。RV 名称到 Catalog opcode 的映射依据编译产物确认，例如 DINTLV 形式的 `vlds` 可以生成单条双结果 `RV_VLDI`。

## 单指令 latency

使用 `初始化输入 → OP → 保存全部结果`，计算：

```text
latency(OP.form) = done(OP_ID) - issue(OP_ID)
```

普通 FP32 加法可使用以下 VF body；外围 kernel、UB 分配与搬运复用有效探针模板：

```cpp
__VEC_SCOPE__ {
    vector_bool all = pset_b32(PAT_ALL);
    vector_f32 a, b, out;
    vlds(a, memA, 0, NORM);
    vlds(b, memB, 0, NORM);
    vadd(out, a, b, all);
    vsts(out, memOut, 0, NORM_B32, all);
}
```

对 LOAD、STORE 使用同样的同 ID 公式，分别更新对应指令的 latency。PSET、比较、转换、双输出与 read-write destination 指令采用自身签名；确保黄金结果能够发现操作数绑定或输出错误。

对 `VLDAS/VLDUS`、`PSTU/VSTAS` 等状态链，额外检查状态代次、指针更新、对齐、mask 与 payload。内部等待可能增加 issue-to-done；先隔离等待，再确定基础 latency 或记录模型近似。普通寄存器转发规则的适用范围不能直接扩展到所有状态链。

## forwarding：依赖链与间隔扫描

目标序列为 `producer(dst) → consumer(..., dst, ...)`：

```text
forwarding(P.form, C.form) = earliest_valid_issue(C) - issue(P)
```

确认 consumer 的其他源和 mask 提前就绪。多输入或 read-write destination 指令分别测目标源位置；编译后核对真实读写寄存器。依赖可以来自向量、谓词或已建模的 align-state。

从短间隔到长间隔改变独立填充组，记录各动态 producer/consumer 的 issue。填充指令也会占用端口，应比较多个间隔与对照用例，定位有效依赖间隔。在循环内关联真实 producer 代次，避免将相邻迭代或同 opcode 的无关指令配对。

若日志观测会受到其他源、调度或队列等待影响，将差值记录为该条件下的观测上界，并继续隔离。特别关注 PSET：紧邻 consumer 的大间隔可能来自向量源尚未就绪。

测量 `LOAD→OP`、`OP→STORE` 时，将参数保存在对应 forwarding pair 中；STORE 自身完成耗时保存在 STORE 的 latency。

## II：同端口独立发射

构造多条无 RAW 的计算流，使源和谓词在目标指令前就绪。独立目的或多组独立 accumulator 的结果均写出，检查编译后目标指令数与依赖。逐步增加流数，观察是否达到稳定发射能力，并检查队列、寄存器和带宽饱和。

按每个 EXU 端口的实际发射顺序取相邻指令：

```text
II(P.form, C.form) = min(valid_issue(C) - valid_issue(P))
```

只有已排除其他阻塞且可重复的样本支持写入这个最小值。测量不同的 P/C 顺序；没有观察到对应指令对时记录缺口。self-II 仅覆盖 `OP→OP`。EXU PERF 的执行事件可辅助分析，写回前检查与模型 issue 时间的映射。

## 执行单元与端口

当前 A5 模型将 FU 分类 `EXU=ALU/SFU` 和端口选择 `dispatch_exu` 分开。已有配置使用 `EXU01`、`EXU0_ONLY`；其他标签先检查当前实现支持范围。

使用密集独立流、竞争指令以及不同顺序检查目标 opcode 的端口分布。只有单端口观测时，记录调度和竞争条件，避免仅凭样本缺失推断硬件限制。归约、双输出等指令分别测量。更换目标架构时重新核对端口合同。

## 运行已有实验工具

以下命令在具有相关脚本的**实验目录根目录**执行。先读取脚本参数与目标环境；`ACL_PATH` 指向实际 CANN 安装。独立运行目录由这些探针创建，输出的 `Artifacts:` 路径保存本次证据。

```bash
python3 skills/vfsim-instruction-benchmark/scripts/run.py --probe vmov_single
python3 skills/vfsim-instruction-benchmark/scripts/run.py --probe vmov_chain
python3 skills/vfsim-instruction-benchmark/scripts/run.py --probe vmov_independent
python3 skills/vfsim-instruction-benchmark/scripts/run.py --probe pset_vadd_gap --gap 12
python3 skills/vfsim-instruction-benchmark/scripts/compute_sweep.py --out /tmp/vfsim-compute-measurement
python3 skills/vfsim-instruction-benchmark/scripts/memory_probe.py --kind vldus_no_update --dtype fp32 --offset 1 --iterations 1
python3 skills/vfsim-instruction-benchmark/scripts/memory_probe.py --kind pstu --offset 0 --iterations 5
```

`compute_sweep.py` 的输出目录应为本次新目录。上述命令分别覆盖复制、谓词和特殊访存，具体测量目标仍按实际动态流确定。

自定义 `.cce` 可复用 `ascend_runner/current/build_native_simexec.sh` 与 `run_native_simexec.sh`。先阅读 host runner 的参数协议、输入生成和 golden，匹配 kernel 参数数量、dtype、shape 与输出语义。该通用 runner 对任意新 kernel 的数值校验能力需要单独核对。`run_native_simexec.sh` 会重建同名运行目录，重跑前归档需要保留的证据，或选用新的实验名称。

`ascend_runner/single_op_param_suite/` 和旧校准脚本中的字段及回填逻辑需要按本 skill 的参数合同检查；复用时仅提取本次需要的动态事件与有效字段。

## 配置结构

以下为当前 schema v2 的最小结构示例；数值取自仓库已有 VADDS FP32 条目，仅用于说明键和方向。新实验使用本次确认的数据。

`configs/isa.json`：

```json
{
  "schema_version": 2,
  "defaults": {"vf_startup_cost": 23, "vf_drain_cost": 12},
  "instructions": {
    "VADDS": {
      "op_class": "COMPUTE",
      "forms": {
        "fp32": {"latency": 7, "EXU": "ALU", "dispatch_exu": "EXU01", "dtype": "fp32"}
      }
    }
  }
}
```

`configs/forwarding.json`：

```json
{
  "schema_version": 2,
  "defaults": 3,
  "forwarding": {"VADDS.fp32": {"VADDS.fp32": 3}}
}
```

外层为 producer，内层为 consumer。`defaults=3` 是缺失 pair 时 producer latency 的减去量；实际 LOAD→STORE 等回退规则读取当前 ParamDB。回退值和兼容 form 命中单独记录为模型策略。

`configs/InitiationInterval.json`：

```json
{
  "schema_version": 2,
  "defaults": 1,
  "InitiationInterval": {"VADDS.fp32": {"VADDS.fp32": 1}}
}
```

外层为同端口前一条指令，内层为当前指令。缺失 pair 的有效值按当前 ParamDB 实现核对；当前重构版使用 latency 差值回退。latency 和 II 使用正整数周期，forwarding 可为零。

按目标条目合并配置，保留其他已确认参数。`form` 与 `dtype` 分别核对；例如 `VOR.b32` 的数据类型为 `uint32`，转换 form 可为 `f32_to_f16`。不要根据 dtype 自动覆盖相近 form 的测量结果。

## 验证与记录

Python 模型目录可运行相关模块测试和 smoke 回归：

```bash
python3 -m unittest tests.test_parameter_loading tests.test_pset_timing -q
python3 tools/run_cost_model_regression.py --tier smoke --out-dir results/benchmark_regression
```

按目标指令选择实际相关测试；纯 C++ 检出按其 CMake 配置运行 CTest。若构建目录为 `build-native`：

```bash
ctest --test-dir build-native --output-on-failure
```

比较 CAModel 与 VFSim 的局部 issue/done、依赖间隔、端口及 VF 总周期。读取 `model_warnings.json` 判断是否使用缺失参数或兼容 form 回退；配置变化后重新创建模型实例。总体误差用于评估近似范围，各字段的确认依靠相应隔离实验。

结果表至少记录：目标配置键、值、单位、实测/近似/未覆盖状态、样本数、差值范围、环境、probe、动态 ID、日志行号、隔离依据及限制。保存失败样本诊断；不将失败或未执行用例计入通过数。

方法来源为实验目录中的 `ascend_runner/operator_test.md`；编译运行流程可查 `ascend_runner/CCE_simulator_src_fanout_probe_guide.md`。字段合同与已知近似以模型目录的代码和配置文档为准。
