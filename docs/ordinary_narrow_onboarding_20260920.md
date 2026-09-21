# 普通窄类型指令接入说明

## 范围

本轮只接入 DV100 上已有 CAModel 数值与流水证据的四个形式：

| canonical 指令形式 | latency | 执行端口 |
| --- | ---: | --- |
| `VCVT_BF16_TO_F32.bf16_to_f32` | 7 | EXU0、EXU1 |
| `VCVT_S32_TO_U8.s32_to_u8` | 7 | EXU0、EXU1 |
| `VDUP.int8` | 6 | EXU0、EXU1 |
| `VBR.int8` | 6 | EXU0、EXU1 |

`int8`/`uint8` 是 canonical dtype 名称，对应 CCE 源码中的 `s8`/`u8`。
原始 CCE、数值验证、动态指令 ID、日志和哈希清单位于
`results/ordinary_extra_stage1_20260920`。

## 前端语义

虚拟 `VCVT` 在 Catalog 参数绑定前，根据目的和源寄存器 dtype 选择 specialization。
因此 BF16 转 F32 使用 `PART_EVEN/PART_ODD` 的五参签名，S32 转 U8 使用
`RS_ENABLE + PART_P0...P3` 的六参签名。选择逻辑适用于所有 conversion
specialization，不在解析器中按具体 opcode 增加分支。

`VBR` 使用真实的二参 broadcast 签名；`VDUP` 继续使用 duplicate 签名。
函数参数和局部标量的 C 类型会传播到 canonical value，避免 `int8_t` 标量被
默认记为 `fp32`。`NORM_B8` 已加入 load/store 模式集合。

## 参数边界

DV100 ISA 只写入已测 latency、ALU 分类和 EXU0/EXU1 eligibility。ALU 是根据
指令功能和流水日志作出的分类；周期及端口来自归档样本。

以下内容没有测量，因此不得写成精确配置：

- 四个形式的 self-II；
- 四个形式到其他指令的 forwarding；
- `VCVT_S32_TO_U8` 的 `RS_DISABLE` 等未测试模式；
- `VLDS/VSTS` 的窄类型专用 latency。

这些查询继续采用 ParamDB 默认值，并输出缺失参数 warning。DV121 不继承本轮
DV100 测量：同名 opcode 的缺失 form 或缺失 opcode 仍走默认 latency 9 并告警。

## 回归覆盖

`tests/test_ordinary_bf16_onboarding.py` 和
`tests/test_ordinary_narrow_onboarding.py` 覆盖归档 CCE 解析、canonical opcode/form、
标量 dtype、DV100 参数、未测参数 warning、核心预测以及 DV121 隔离。生成的 C++
Catalog 与 Python Catalog 同源，native smoke test 直接检查相同参数和 warning。

本分支为单 SoC A5 版本，使用根目录 configs 和 dispatch_exu=EXU01；DV121 隔离测试仅在 MultiSoC 分支运行。
