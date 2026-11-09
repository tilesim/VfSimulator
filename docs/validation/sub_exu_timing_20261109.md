# A5 子 EXU 时序回归

分支：`wch/VfSimulator-refactor`。资源模型按 `execution_resource` 和 self-II 管理子单元接收时间，
通过显式例外表达偏离分组公式的指令对。模型规则见[子 EXU 模型](../execution_resources.md)。

## 验证范围

- 25 个 manifest 用例和 12 个 Softmax CCE，共 37 个用例；其中 33 个有归档的 CAModel 参考周期。
- 另检查 11 个顶层 Canonical 输入。48 个输入均成功完成，Python/C++ 的执行周期、VF 结束周期和静态条目数一致。
- Python 完整测试和 8 项 CTest 覆盖资源共享、跨类型、例外穿插、队列选择、参数格式及完整输出对照。
- 753 项既有指令对查询值逐项保持一致；参数来源还包含 9 项已归档的混合 dtype 数学指令 II。
- 每 EXU inflight=7、每周期接收一条、向量和谓词各一个完成名额参与所有对照。

参考数据使用仓库已有 A5 CANN CAModel 记录。模型对照基点为 `96e16636be1b25a7581283df72f4a99da61c56ff`。
参数、队列和发射状态共同构成本次模型，精度统计以 `vf_end_cycle` 对照参考周期。
[机器结果](../data/sub_exu_timing_regression_20261109.json) 保存各输入摘要、配置摘要、周期和 warning。

## 误差统计

| 类别 | 参考用例数 | 成对时序 MAPE | 子 EXU MAPE | 成对时序 WAPE | 子 EXU WAPE |
| --- | ---: | ---: | ---: | ---: | ---: |
| GeLU | 7 | 7.37% | 6.21% | 8.19% | 5.95% |
| SiLU | 3 | 4.87% | 7.56% | 6.09% | 9.41% |
| SwiGLU | 3 | 2.98% | 2.19% | 2.77% | 1.49% |
| Softmax | 12 | 3.39% | 2.70% | 3.35% | 2.66% |
| 其他 | 8 | 2.77% | 2.74% | 3.33% | 3.33% |
| 全部 | 33 | 4.18% | 3.85% | 3.57% | 3.45% |

33 个有参考的用例中，16 个误差减小、10 个增大、7 个持平。37 个用例中有 28 个预测周期变化。
GeLU、SwiGLU 和 Softmax 的分组平均误差减小，SiLU 的分组平均误差增大。
SiLU 的 I=64/96 用例需要继续核对各阶段发射与资源阻塞；该结果保留在回归记录中。

## 逐用例周期

| 用例 | CAModel | 成对时序 | 子 EXU | 周期变化 |
| --- | ---: | ---: | ---: | ---: |
| gelu_poly_i16_u1 | 446 | 408 | 409 | +1 |
| gelu_poly_i64_u1 | 1502 | 1331 | 1360 | +29 |
| gelu_poly_i96_u1 | 2245 | 1947 | 1992 | +45 |
| gelu_i16_u1 | 187 | 191 | 200 | +9 |
| online_update_i64_u1 | 443 | 448 | 439 | -9 |
| probe_src_fanout | 285 | 282 | 282 | +0 |
| probe_branch_live_range | 221 | 218 | 218 | +0 |
| probe_store_capture_reuse | 319 | 317 | 317 | +0 |
| vadds_longchain_i16_1x512 | 21823 | 22153 | 22153 | +0 |
| vadds_longchain_i64_8x64 | 37281 | 39536 | 39536 | +0 |
| vadds_longchain_i128_128x4 | 36641 | 32827 | 32827 | +0 |
| vexp_longchain_i16_1x512 | 95492 | 95492 | 95492 | +0 |
| vexp_longchain_i64_8x64 | — | 161198 | 161198 | +0 |
| vexp_longchain_i128_128x4 | — | 131137 | 131137 | +0 |
| gelu_poly_i96_u2 | 2021 | 1921 | 1943 | +22 |
| gelu_poly_i96_u3 | — | 1880 | 1801 | -79 |
| gelu_poly_i96_u4 | 1712 | 1886 | 1747 | -139 |
| gelu_poly_i96_u6 | — | 1732 | 1685 | -47 |
| gelu_poly_i96_u8 | 1718 | 1738 | 1691 | -47 |
| silu_i16_u1 | 160 | 159 | 163 | +4 |
| swiglu_i16_u1 | 180 | 174 | 172 | -2 |
| silu_i64_u1 | 386 | 412 | 419 | +7 |
| silu_i96_u1 | 538 | 577 | 604 | +27 |
| swiglu_i64_u1 | 480 | 463 | 474 | +11 |
| swiglu_i96_u1 | 678 | 664 | 672 | +8 |
| softmax_expdif_mulcvt_u1 | 713 | 672 | 682 | +10 |
| softmax_expdif_mulcvt_u2 | 609 | 636 | 627 | -9 |
| softmax_expdif_mulcvt_u4 | 594 | 635 | 626 | -9 |
| softmax_expdif_vcvt_u1 | 615 | 658 | 662 | +4 |
| softmax_expdif_vcvt_u2 | 622 | 622 | 617 | -5 |
| softmax_expdif_vcvt_u4 | 618 | 630 | 615 | -15 |
| softmax_sub_exp_mulcvt_u1 | 701 | 714 | 722 | +8 |
| softmax_sub_exp_mulcvt_u2 | 668 | 692 | 675 | -17 |
| softmax_sub_exp_mulcvt_u4 | 660 | 697 | 680 | -17 |
| softmax_sub_exp_vcvt_u1 | 686 | 699 | 690 | -9 |
| softmax_sub_exp_vcvt_u2 | 681 | 681 | 671 | -10 |
| softmax_sub_exp_vcvt_u4 | 682 | 694 | 671 | -23 |

4 个缺少 CAModel 参考的用例参加执行与两种实现一致性检查，误差统计使用其余 33 个用例。
全部 48 个输入的模型 warning 列表为空。完整指令测量范围及未分组 form 的回退行为见模型说明。
