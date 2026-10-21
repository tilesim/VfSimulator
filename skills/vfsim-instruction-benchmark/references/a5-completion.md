# A5 SIMD VF 完整覆盖实验

目标为 CANN `9.0.0-beta.1`、dav-c310-vec、Ascend950PR_9599，目标宏为 3101。
以安装版的通用与 `npu_arch_3101` VF 头文件为签名依据；头文件 SHA256 保存在
`configs/cce_lowering.json` 的 target 与 A5 清单中。

## 数值参考

普通计算、归约和进位使用独立标量运算；64-bit 双向量通过整数拆分/组合维护溢出及符号。
整数除法和余数的探针排除除零与未定义的最小有符号值除 -1。
浮点转换区分源/目的布局、PART/PP、饱和与舍入方向；低精度使用有限编码表。
ROUND_A 在中点处远离零，ROUND_R 使用 ties-to-even；向 float32 的方向舍入单独检查边界。
HiF8 非有限值与 ROUND_H 的特殊边界尚未穷尽。

MOVVP 的 part 选择向量中一个 16B/8B 块，每个 bit 扩展为 2/4 个 predicate bit，
完整 32B 位图用 PSTS 保存。该含义与
[MaskGenWithRegTensor](https://www.hiascend.com/document/detail/en/CANNCommunityEdition/910/API/ascendcopapi/docs/en/api/SIMD-API/basic_api/reg_vector_compute/reg_data_load/MaskGenWithRegTensor_MaskReg.md)
的类型分组说明对应。该链接用于语义核对，实验版本仍为 9.0.0-beta.1。

分布模式按独立字节布局构造 golden。SPLT/MRG 的 channel 划分以 32B block 为单位，
另使用不同随机种子的逆变换 roundtrip 校验。谓词 US 重复 bit、DS 间隔选择 bit、
PK 打包 bit；PLDI/PSTI 的立即数按 16B/32B 单位换算，标量偏移按字节换算。
VAG 的一至四层步幅映射到硬件循环迭代序号。

所有可定义输出与保护区按字节检查。部分 mask 的 UNKNOWN 输出只观察有定义的有效位置，
并记录定义字节数。BF16 标量 MERGING wrapper 的旧值局部变量未初始化，
探针只观察有效 lane。配对 VAND/VOR/VXOR/VNOT 的 MERGING wrapper 使用头文件实际的选择方向。
原生宽度非对齐 vstore 丢弃 append 返回指针的用例保留为数值诊断，配对 wrapper 的
返回指针链单独验证。

## 参数提取

计算 latency 使用同动态 ID 的 popped/done；普通访存使用 LSU I1/done，保留排队差值。
原始 LSU 与 GSU 日志和 SHA256 随实验归档。共享 GSU/块存储服务间隔使用固定索引/步幅
的连续请求，同时保留孤立请求。结果仅适用于记录的访问分布。

forwarding 要求真实 producer/consumer、其他源在 producer 发射时已就绪，并核对 IDU 源清单。
II 使用同端口独立发射，单端口限制另有端口竞争探针。MOVVP 测得 latency 16、II 4、EXU0_ONLY。
四条旧 Pd carry-in 未出现在 IDU 清单中，保留未确认。

VDUPI 的两部分各自有 issue/done；模型的拼接零间隔边仅保证 SSA 拼接顺序。
高部的 EXU0 资格尚未独立隔离，当前端口配置包含近似。
VSTUS_BYTES/VSTAS_BYTES 的完整向量 latency 为 9，部分/零计数尾块观察到 8。

## 软件规则

`extract_cce_software_recipes.py` 编译并优化 CANN wrapper 的 LLVM IR；
`generate_cce_software_lowering.py` 将物理操作及 SSA 数据流转换为 224 条规则、2505 个步骤。
包括配对向量、整数除法/余数和 58 条原生无谓词整数转换规则。
每条规则有独立数值通过的样本，并与 Native 的完整 Canonical JSON 和仿真周期对照。
实际后端可删除、复制或 spill 寄存器；规则不承诺逐条复现最终 VF 汇编。

## 复现

```bash
python3 tools/validate_a5_cce_signatures.py --out /tmp/a5-signatures
python3 tools/extract_cce_software_recipes.py --cann /path/to/cann --out /tmp/a5-recipes
python3 tools/generate_cce_software_lowering.py --input /tmp/a5-recipes
python3 tools/generate_cce_lowering_cpp.py configs/cce_lowering.json native/api/generated/CceLoweringData.inc
python3 tools/validate_cce_lowering.py --build-dir /tmp/vfsim-build --out-dir /tmp/a5-recipe-check
python3 skills/vfsim-instruction-benchmark/scripts/vector_software.py --out /tmp/a5-software
python3 skills/vfsim-instruction-benchmark/scripts/vector_conversion.py --native-unmasked --out /tmp/a5-native-cvt
python3 skills/vfsim-instruction-benchmark/scripts/predicate_memory.py --post --out /tmp/a5-predicate-post
python3 skills/vfsim-instruction-benchmark/scripts/predicate_memory.py --literal --out /tmp/a5-predicate-immediate
python3 skills/vfsim-instruction-benchmark/scripts/vector_special.py --operation movvp --dtypes uint16 uint32 --port-pressure --out /tmp/a5-movvp
```

各生成器的 `--help` 提供 dtype、mode、源位置、索引和端口竞争选项。
`archive_batches.py` 保存实验的有效日志、源码和输入输出，并生成 SHA256 清单。
原始运行目录为 `results/a5_instruction_completion_20261020/`；精简证据保存在本目录的 `evidence/a5-*.json`。

## 工具链诊断

目标编译器拒绝 VSADD/VSADDS/VSSUB、PSLIDE_B8/B16/B32、VNOP 和 VNOPXN。
CAModel 缺少四种 FP16/BF16→FP8 与四种 VSELRV2 的 VALU 条目。
VSTUR/VSTAR 的 NOPOST、POST、循环与已初始化状态对照在 CAModel 中超时。
这些样本保留编译、运行和部分动态日志，不产生已确认的参数。
具体 form 和未测 pair 见 `docs/instruction_coverage/a5/progress.json`。
