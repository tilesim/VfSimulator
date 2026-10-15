# Canonical 测试输入

这些 JSON 文件保存固定的 Canonical 程序，供输入校验、动态展开和周期测试使用。

| 文件或目录 | 内容 |
| --- | --- |
| `v1_valid_loop.json` | 单循环加载及默认周期 |
| `v1_valid_loop_carried.json` | 循环 entry、back-edge 和 exit 定义 |
| `v1_valid_top_blocks.json` | 顶层循环、尾部指令和屏障 |
| `v1_valid_empty_top_block.json` | 零次循环和后续 block |
| `v1_invalid_loop_scope.json` | 循环回边作用域错误 |
| `v2_external_predicate_forms.json` | MOVVP、PSTU、VSTAS 的 b16/b32 与 uint16/uint32 form 合同 |
| `v2_plt_packed_fp16.json` | PLT 两个结果和 FP16 打包访存 |
| `timing/` | 寄存器、访存、屏障、unroll 和缺失参数诊断的执行用例 |

form 描述指令形式，value.dtype 描述数据类型。谓词 fixture 显式提供 producer，
对齐状态通过 append/consume 属性表达，PSTU 提供指针更新和打包 span。
fixture 经 API 读取和校验后传给 Core。非法 fixture 用于检查诊断及拒绝边界。
