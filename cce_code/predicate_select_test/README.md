# Predicate 数值验证

输入为两个长度 64 的 FP32 向量，输出为 `A > B ? A + 1 : B - 1`。
CCE 源码是上一级的 `predicate_select_fp32.cce`。

`vcmp_gt` 产生动态谓词，`vsel` 消费该谓词。这里验证的是 CAModel
计算结果与独立 golden 的数值一致性，不是 VfSim 时间预测精度，
也不能仅凭该测试判断物理谓词寄存器容量或重命名机制。

运行：

```bash
python3 cce_code/predicate_select_test/run.py
```

脚本在独立 `/tmp/vfsim-predicate-select-*` 目录中编译、运行并保存日志，
可通过 `ACL_PATH` 指定 CANN 安装位置。目标为 A5 `dav-c310-vec`，
CAModel 型号为 `Ascend950PR_9599`。

## 2026-09-22 验证结果

- 64 个元素逐位一致，最大绝对误差为 0。
- 覆盖 22 个 `A > B`、21 个 `A < B`、21 个 `A == B`。
- 指令日志确认 `RV_VCMP_GT` 写 `Pd[2]`，`RV_VSEL` 读 `Pg[2]`。
- 归档：`results/predicate_select_fp32/camodel_20260922/`，含
  `input.bin`、`golden.bin`、`output.bin`、`validation.json`、编译日志和 core0 日志。
