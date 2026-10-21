# A5 Membar 归档算子指令对

目标为 CANN `9.0.0-beta.1`、`Ascend950PR_9599`、`dav-c310-vec`、架构宏 `3101`。
实验测量归档算子的计算与访存指令对。屏障本体的独立时序需要相应结构实验。
覆盖状态见 [归档算子报告](../../../docs/instruction_coverage/a5/membar_pairs.md)。

## 清单与输入合同

按归档源文件和 Canonical 语义分别去重，保存原始文件 SHA256、kernel 名称与输入接受状态。
同一程序内具有共同 EXU 端口的 COMPUTE form 构成有向 II 需求矩阵。
静态 RAW 使用归档 value 的 producer 身份；可运行输入另统计完整执行过程中实际查询的参数。
静态 RAW 保留循环携带依赖的验证边界。

归档 Canonical 的元数据检查保存输入的 schema 拒绝记录。
CCE、Canonical、输入合同及 Native 校验的阻塞项分别列入状态清单。
A6 材料按 A5 配置核对通用指令键；A6 架构及其时序单独验证。

## 独立数值验证

每个案例使用 4 KiB 输入区和独立输出区。输出初值与输入不同，逐个保存目标结果，
检查未写区域与哨兵字节。最多 16 个案例时输出 UB 起点为 `0x10000`；
17–28 个案例使用 `0x20000`，28 个案例末尾为 `0x3c000`。
目标平台 UB 大小为 253952 byte，保留末尾 8192 byte 栈空间。

FP32/FP16 等计算结果使用规定的数值容差。谓词、整数索引、输出位置与保护区精确比较。
LOAD 和复制类 producer 的输出按字节核对。
STORE、VMOV、VDUP、VSEL 的 consumer 同时检查其与已独立验证的 producer 的位保持关系。
谓词控制复制时使用已验证的谓词位及原始输入，检查有效与无效 lane。

归约保存已定义的前几个结果 lane。VCMAX 同时保存值与整数索引。
归约结果作为普通向量源时，consumer 使用有效 lane 的 mask；广播使用最低有效元素。
LN、SQRT、DIV 的参考输入保持定义域有效；SUB→LN 使用正的差值。
B32 广播使用非零最低元素，使部分 mask 的有效和无效输出均可观察。

## II 隔离

`operator_pairs.py` 生成独立输入、成对交错和反向交错流。
结果可立即存储以缩短谓词活跃区间。六条 PAND 流使用不同的谓词操作数组合，
PLT 计数和 VCI 起点可在 VF 外从 UB 读取，并同步 PIPE_S。
预热链的操作、长度和端口相位随样本保存。

分析按同一端口的实际相邻计算指令进行，包含支持计算指令的位置。
1 cycle 观测证明该端口的最小发射间隔下界。
多周期观测要求当前源已就绪、IDU 提前分发、同 opcode/form 的历史 self-II 已释放，
并排除在途容量及更早无关指令的完成冲突。
相邻两条指令自身的完成冲突记录为有效转换间隔的组成部分。
每个可用端口至少两次重现相同最小间隔，两个端口的最小值一致后录入。

## forwarding 隔离

`membar_forwarding.py` 按语义源位置生成探针：二元左右源、普通向量源、mask、
PAND 的两个数据谓词与 mask、VAXPY 的旧目的与向量源、STORE 数据、GSU 数据与索引。
VAXPY 两条向量依赖路径分别初始化和验证。
标量广播使用其实际 mask 作为就绪门。VSEL 寄存器源采用混合选择 mask，
使左右向量源都能影响输出。未影响输出的源路径保留数值结果，单列其测量资格。

producer 与 consumer 的动态 ID、opcode 及 ISU 物理寄存器必须对应真实 RAW。
其他源在 producer 发射前完成；consumer 的 IDU 分发提前，硬件源就绪时刻等于其发射时刻。
LOAD producer 的 popped 必须等于 LSU I1，排除模块入口前的排队。
STORE 和 GSU 存在接收时刻时，同样核对该时刻与 popped。
GSU_RECV 和 STU_IB_BUF.RECV 按日志的一个周期偏移换算到统一口径。
GSU 接收之后的固定流水级保留在该模式的延迟中。

每个语义源位置至少两次重现最小间隔，pair 参数取各源最小值的最大值。
GSU 使用有界的连续、间隔索引及部分 mask，记录 32-bit 数据视图。
任意索引模式的 bank 冲突需要额外建模与实验。

## 复现

实验目录包含原始清单与各阶段探针。仓库根目录可使用：

```bash
python3 skills/vfsim-instruction-benchmark/scripts/operator_pairs.py --cases results/membar_pairs_20261021/ii_cases.json --out /tmp/membar-ii --jobs 2 --batch-size 16 --run-timeout 1200
python3 skills/vfsim-instruction-benchmark/scripts/membar_forwarding.py --cases results/membar_pairs_20261021/forwarding_cases.json --out /tmp/membar-fwd --batch-size 28 --run-timeout 1200
PYTHONPATH=. python3 skills/vfsim-instruction-benchmark/scripts/analyze_membar_pairs.py --runs /tmp/membar-ii /tmp/membar-fwd --out /tmp/membar-parameters.json
```

定向复测从对应归档批次的 `cases.json` 读取顺序、预热、独立流和源位置。
使用 `cases.corrected.json` 时，保留其与原始元数据的修正说明。
`validation.strict.json` 保存追加位保持校验的结果，原始校验记录保留。

`raw_runs.tar.gz` 保存 CCE、host、编译产物、输入、golden、输出、命令和活动向量核日志。
可在解出的批次目录重放保存的二进制：

```bash
VFSIM_CANN_DIR=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1
ASCEND_TOOLKIT_HOME="$VFSIM_CANN_DIR" NPU_TYPE=Ascend950PR_9599 LD_LIBRARY_PATH="$VFSIM_CANN_DIR/tools/simulator/Ascend950PR_9599/lib:$VFSIM_CANN_DIR/lib64:$VFSIM_CANN_DIR/x86_64-linux/devlib:$VFSIM_CANN_DIR/x86_64-linux/devlib/device" ./host ./kernel.o vector_compute
```

失败样本保存原因并排除出实测表项。同编码继承项记录来源 form，
与实测项分别计数。写入共享配置后使用新模型实例重新读取参数，
核对固定输入的 Python/C++ 周期和发射、完成时刻，并报告误差指标与输入阻塞项。
