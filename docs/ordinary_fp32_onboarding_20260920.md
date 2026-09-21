# DV100 普通 FP32 指令参数接入

2026-09-21 从 MultiSoC-membar 提交 `2821de5` 同步这批参数及必要的指令接入代码。
该提交恢复了原先保存在 stash `c511699` 中的 2026-09-20 测试结果。
本次按目标分支已有的配置结构和 API 适配，不迁移来源分支的其他功能。

## 已测参数

来源为 CANN 9.0.0 beta.1、Ascend950PR_9599 simulator、dav-c310-vec 的
CAmodel 日志，不是硅片实测；本次没有重新运行 CAmodel。
开始时间采用 popped/EXQ issue 口径。

| DV100 指令 | latency | VLDS.fp32 到指令 forwarding | 自身 forwarding | 到 VSTS.fp32 forwarding | self-II |
| --- | ---: | ---: | ---: | ---: | ---: |
| VSQRT.fp32 | 17 | 6 | 14 | 15 | 4 |
| VLN.fp32 | 18 | 6 | 15 | 16 | 4 |

两条指令均允许 EXU0/EXU1，功能分类为 SFU。端口来自日志，SFU 为建模分类。
仅回填 DV100 的 fp32；未测精度、跨指令对和 DV121 参数继续保留 fallback。

## 配套文件

- 参数：`configs/{isa,forwarding,InitiationInterval}.json`。
- 语义：共享 `configs/instruction_catalog.json` 及生成的 Native 目录。
- 测试：`tests/test_ordinary_fp32_onboarding.py`，包含 Python/Native 周期对齐。
- 原始报告和证据：`results/ordinary_fp32_stage1_20260920/`；该目录被 Git 忽略，
  不随普通 clone 分发，本文记录参数来源而不声明日志已纳入版本管理。

同一 stash 中的 VOR/VCI、BF16 与窄类型接入一并恢复，范围见
`ordinary_vor_vci_onboarding_20260920.md` 和 `ordinary_narrow_onboarding_20260920.md`。
loop 切分优化和其他无关改动仍留在 stash 中。

本分支为单 SoC A5 版本，使用根目录 configs 和 dispatch_exu=EXU01；DV121 隔离测试仅在 MultiSoC 分支运行。
