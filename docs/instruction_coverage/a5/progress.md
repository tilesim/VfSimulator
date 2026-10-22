# A5 SIMD VF 覆盖进度

目标为 CANN `9.0.0-beta.1`、Ascend950PR_9599、`dav-c310-vec`、
`__NPU_ARCH__=3101`。接口来源、头文件哈希和配置哈希保存在
[机器清单](coverage.json)，[进度数据](progress.json) 保存参数及验证统计。

## 接口与类型

| 项目 | 数量 / 范围 |
| --- | --- |
| 头文件非标量 VF API 名称 | 160 |
| 完成入口适配的 API 名称 | 153 |
| 去重函数签名 | 1426 |
| 代表性调用通过 adapter 检查 | 1422 |
| 目标编译器拒绝的 API 名称 | 7 |
| CANN 软件展开规则 | 224，合计 2505 个物理指令步骤 |
| 软件规则数值验证 | 224 / 224 |
| 软件规则 Python/C++ Canonical 与周期对照 | 224 / 224 |

153 个 API 的代表性 dtype/form 调用均完成解析与校验。逐签名检查包含三个
PSLIDE 拼写的语义检查；其 builtin 被目标编译器拒绝。VNOP、VSADD、VSADDS、
VSSUB 的代表性调用保留为拒绝项。VNOPXN 是单列宏接口，同样被目标编译器拒绝。
[逐签名结果](signatures.json) 不代表所有 template 常量和运行时输入组合均已验证。

Python/C++ 共用 Catalog、软件展开表和时序配置。普通计算、比较、归约、进位、长乘、
选择、重排、谓词、低精度转换、广播、直访/索引访存、VAG、非对齐状态及四种向量屏障
均有语义实现。scalar-only API 在统计范围外。

配对向量的计算按前端 LLVM IR 展开，vload/vstore 和配对 gather/scatter 显式建立
多个访存及中间索引。MODE_MERGING 保留旧目的依赖。向量源 VDUP 和标量源 VDUPS
分别建模。MEMBAR 约束对应方向的 LOAD/STORE；VV_ALL 允许计算按普通依赖发射。

## 参数与数值证据

| 配置统计 | 数量 |
| --- | ---: |
| Catalog opcode，含虚拟转换入口 | 227 |
| 非虚拟 opcode/form，含兼容位宽 form | 1117 |
| 类型与物理辅助 form | 1011 |
| 显式 latency，含兼容 form | 981 |
| 类型与物理辅助 form 缺少 latency | 41 |
| 类型与物理辅助 COMPUTE form 缺少 self-II | 11 |
| forwarding 配置 pair，含 wildcard | 1699 |
| II 配置 pair，含 wildcard | 2506 |

显式配置、实测、相同编码继承和未测分别记录。相同 opcode/form 没有合法 RAW 的
指令不要求 self-forwarding，例如 PSET、PLT/PLTM、VBR、VCI、VDUPS、MOVVP。
完整 producer/consumer 笛卡尔积的交叉 forwarding 和 II 尚未逐对测量。

主要测量范围如下；逐事件与配置变化保存在 skill 的 `references/evidence/`：

- Membar 归档的 A5 算子覆盖 992 组 II、325 组静态 RAW 查询；新增 640/169 组实测 II/forwarding，并分别补充 31/35 组同编码继承。48 组可运行 A5 程序无参数 warning；旧 GSU B32 拼写的两类静态兼容诊断单列。见 [归档算子指令对](membar_pairs.md)。

- VAG 访存 `VLD.uint16→VST.uint16` 的 Vd 转发为 8 cycle，8 个数值案例通过，4 个隔离样本一致；见 [访存转发测量](https://github.com/tilesim/VfSimulator/blob/4cc20155f36be92e24772e4909bd7b3148c6e389/skills/vfsim-instruction-benchmark/references/memory-forwarding.md)。

- VFtest 完整算子与依赖探针的 74 个输入文件覆盖 325 组 II、103 组实际 RAW forwarding；补测新增 162 组 II 和 10 组 forwarding，686 个有效数值案例通过。覆盖口径见 [算子指令对](operator_pairs.md)。
- 常用跨指令对确认 99 组 forwarding、70 组 II，新增 33/26 个精确参数；478 个数值案例通过，覆盖原生 FP32、FP16、BF16。
- 普通计算 42 类接口、295 个 opcode/dtype 组合，具有 latency、self-II 和所需源位置的 self-forwarding。
- 归约、多结果和选择 15 类接口、84 个组合，具有 latency/self-II；80 个具有完整 self-forwarding。
- 基础转换 37 种、VPACK 4 种、VUNPACK 4 种，具有 latency、self-II 和转换视图 RAW；低精度及快速转换另有扩展记录。
- 224 条软件规则均有独立数值验证，包含 58 条原生无谓词整数转换规则。
- 谓词交织、打包、范围生成、广播、重排、指数差、直方图和 MOVVP 均有相应探针。
- VLDS/VLD 的 21 种分布与 VSTS/VST 的 13 种分布均通过字节布局校验；SPLT/MRG 另有不同随机输入的逆变换校验。
- 谓词访存 15 个 API/mode 组合、10 个 POST_UPDATE 组合、5 个立即数偏移组合通过数值与保护区校验。
- 无掩码 VGATHERB 的 15 种合法 dtype 通过数值校验；该目标没有无掩码 uint64 重载。

[指令时序](../../instruction_timing.md) 汇总测量口径、主要数值与查询行为。
[实验方法与限制](https://github.com/tilesim/VfSimulator/blob/4cc20155f36be92e24772e4909bd7b3148c6e389/skills/vfsim-instruction-benchmark/references/a5-completion.md)
给出参考值定义域和复现命令。

## 保留的参数缺口

| 原因 | form 数 | 缺少项目 | 证据与处理 |
| --- | ---: | --- | --- |
| PSLIDE B8/B16/B32 被目标编译器拒绝 | 3 | latency、self-II、forwarding | 保留声明与编译诊断 |
| FP16/BF16→FP8 E4M3/E5M2 缺少 CAModel VALU | 4 | latency、self-II、forwarding | 保留可编译调用及 CAModel abort 日志 |
| VSELRV2 四种原生类型缺少 CAModel VALU | 4 | latency、self-II、forwarding | 保留可编译调用及 CAModel abort 日志 |
| VSTUR/VSTAR 状态探针未取得可完成的 CAModel 运行 | 30 | latency 与相应访存时序 | 保留初始化、NOPOST/POST 和循环对照及超时日志 |

以上合计 **41 个 latency form、11 个 COMPUTE self-II form**。
参数表保留缺失及 warning，未将失败样本写为已确认参数。

VADDCS/VSUBCS 的 S32/U32 旧 Pd carry-in 未出现在该 CAModel IDU 源清单中，
四条完整 self-forwarding 路径仍未确认。其 latency、II、向量和 mask 路径已有记录。
VST_VST/VV_ALL 已实现依赖等待，独立屏障时序开销保留未测。

## 适用范围与近似

GSU 的延迟和服务间隔对应已测索引模式；任意运行时索引的 bank 冲突不由这些固定值保证。
VSSTB 服务间隔对应块步幅 2。部分/零计数 byte helper STORE 观测为 8，
完整向量默认 latency 9 对部分尾块可能多估一周期。
VDUPI→VDUPI_HI 的零间隔表达值拼接顺序；高部的端口资格保留近似标记。

软件规则来源为优化后的前端 LLVM IR。最终后端的指令删除、寄存器复制、spill 和调度
可能改变完整 VF 的实际指令流。CCE parser 支持其明确声明的语法合同；跨元素宽度的
UB 指针 cast 与任意 C++ 指针表达式不在本次逐签名覆盖保证内。

CANN wrapper 的以下行为单独保留：原生宽度非对齐 vstore 丢弃 append 返回的指针；
配对位运算的 MERGING wrapper 在有效 lane 选择旧目的；BF16 标量 MERGING wrapper
读取未初始化局部值。模型记录对应依赖，数值探针只检查具有定义的输出。
低精度 golden 覆盖可明确编码的有限值及已测舍入边界，NaN/Inf 和 HiF8 特殊舍入不作穷尽保证。

## 回归与原始产物

416 项 Python 测试、8 项 C++ CTest、224 条软件规则对照和 22 个 Native smoke case
通过。smoke 的 VF 周期与既有基线一致。

本地原始实验保存于 `results/a5_instruction_completion_20261020/`，
`manifest.json` 记录文件 SHA256，包含成功样本与诊断样本。目录同时保存源码、输入、
golden、输出、编译/运行命令、popped/done、EXU、LSU、GSU 和 IDU 日志。
该目录按仓库约定由 Git 忽略；精简事件证据、配置、验证工具和测试 fixture 参与版本管理。

常用跨指令对的原始实验保存于 `results/a5_common_pairs_20261020/`，
参数、逐事件与重复样本见 [常用指令对测量](https://github.com/tilesim/VfSimulator/blob/4cc20155f36be92e24772e4909bd7b3148c6e389/skills/vfsim-instruction-benchmark/references/common-instruction-pairs.md)。
混合 FU 历史的候选修正及精度变化单独保存在该目录的 `scheduler-review/`。

FP32 执行资源补测包含 52 个数值案例与 936 条目标指令，覆盖 VCMAX/VADD/VEXP
的四种交错模式、单条穿插，以及 ALU 和数学 SFU 对照。self-II 与共享资源阻塞分别记录，
结果见 [FP32 执行资源实验](https://github.com/tilesim/VfSimulator/blob/4cc20155f36be92e24772e4909bd7b3148c6e389/skills/vfsim-instruction-benchmark/references/fp32-fu-contention.md)。
原始产物保存在 `results/a5_fp32_fu_contention_20261020/`；本批未修改时序配置或调度逻辑。
