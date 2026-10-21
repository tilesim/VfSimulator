# Membar 归档算子指令对覆盖

目标为 CANN `9.0.0-beta.1`、`Ascend950PR_9599`、`dav-c310-vec`，架构宏为 `3101`。
范围为 `membar_results` 归档中的向量算子；Python 与 Native C++ 共同读取时序配置。

## 查询覆盖

| A5 范围 | 数量 |
| --- | ---: |
| CCE / 归档 Canonical 文件 | 549 / 319 |
| 原始版本 / 算子分组 | 153 / 32 |
| 静态语义程序 / 可完整运行程序 | 305 / 48 |
| opcode/form / COMPUTE form | 55 / 40 |
| 各输入共享端口的有向 II 矩阵并集 | 992 |
| 归档 value producer 图的静态 RAW 指令对 | 325 |

992 组 II 和 325 组静态 RAW 查询均取得配置值或已标定的 producer 规则。
同一输入内计算 form 构造有向矩阵，再按共同端口筛选；完整 40×40 矩阵具有独立测量范围。
RAW 按归档 value 的 producer 身份枚举。静态元数据检查保留循环携带依赖的验证边界。

| 配置类别 | 新增实测表项 | 同 B32 编码继承 |
| --- | ---: | ---: |
| ii | 640 | 31 |
| forwarding | 169 | 35 |
| isa | 0 | 1 |

B32 继承项记录来源 form，覆盖 VDUP 的位宽表示及 GSU 的 FP32/S32/U32 形式。
归档 VGATHER2/VSCATTER 的 b32 拼写沿用兼容查询并保留诊断，其合法 typed forms 使用同 B32 编码的转发表项。
继承项保留独立的证据类型。
测量覆盖谓词 mask、二元左右源、VAXPY 旧目的与向量源、归约有效 lane、转换、LOAD/STORE 和 GSU。

## 测量证据

2367 个数值样本通过。数值结果、目标 RV 数量及真实物理 RAW 先完成检查，
随后检查源就绪、同端口相邻发射、self-II 历史、IDU 分发、在途占用和完成冲突。
每个 II 可用端口至少两次重现最小间隔；每个 forwarding 语义源路径至少两次重现最小值，
pair 配置取各源最小值的最大值。

LOAD 要求 popped 与 LSU I1 相同。GSU 使用 GSU_RECV 接收时刻；队列等待单列。
归约保存硬件定义的结果 lane；VCMAX 索引与保护区精确比较。
LOAD、STORE 和寄存器复制额外检查位保持关系，浮点计算使用规定的数值容差。
编译失败、参考值失败、数值失败和未隔离样本保留诊断并排除出参数录入。

方法及复现命令见 [benchmark skill](https://github.com/tilesim/VfSimulator/blob/4cc20155f36be92e24772e4909bd7b3148c6e389/skills/vfsim-instruction-benchmark/references/membar-pairs.md)，
逐参数证据见 [实验证据](https://github.com/tilesim/VfSimulator/blob/4cc20155f36be92e24772e4909bd7b3148c6e389/skills/vfsim-instruction-benchmark/references/evidence/a5-membar-pairs.json)，
逐版本状态见 [机器清单](membar_pairs.json)。
审计输入快照保存在 `results/membar_pairs_20261021/canonical_inputs.tar.gz`。
源码、命令、二进制、输入、golden、输出及原始日志保存在
`results/membar_pairs_20261021/raw_runs.tar.gz`，归档 SHA256 记于同目录 `summary.json`。

## 回归

核心保持每 EXU inflight=7、opcode/form self-II 历史及每 EXU 每周期一条完成。
API、Core、Native 运行时源码按 SHA256 核对。
457 项 Python 测试通过（18 项跳过），8 项 CTest 通过。
37 组固定 Canonical 输入逐条核对 Python/C++ 发射、完成周期，均一致。
33 组具有 CAModel 参考值，其余 4 组检查执行及两种实现的一致性。

| 集合 | 有参考案例 | 补参前 MAPE | 补参后 MAPE | 补参前 WAPE | 补参后 WAPE |
| --- | ---: | ---: | ---: | ---: | ---: |
| standard | 21 | 4.632% | 4.632% | 3.575% | 3.575% |
| softmax | 12 | 3.394% | 3.394% | 3.351% | 3.351% |
| all | 33 | 4.182% | 4.182% | 3.567% | 3.567% |

归档中的 48 组 A5 程序完整运行，指令参数缺失与兼容查询 warning 为 0。
全归档可运行的 59 组输入中，Native C++ 完成 52 组，周期与 Python 一致。
7 组仿射变量作用域校验拒绝单列，其中 A5 两组、A6 材料五组。

## 输入和架构范围

153 个 A5 原始版本中，37 个具有可运行输入，103 个提供静态元数据检查，13 个受输入合同限制。
归档 JSON 的 schema、CCE 语法和历史编译失败分别记录。原归档内容保持固定。
A6 材料按 A5 配置检查参数键并补测通用指令对，A6 时序和硬件架构单独验证。
GSU 数值与时序对应已测索引分布；任意索引的 bank 冲突需要相应结构实验。
屏障本体时序单独保留未测项。
