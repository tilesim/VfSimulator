# A5 VINTLV / VDINTLV

## 实测证据

CCE `vintlv(dst0,dst1,src0,src1)` 和 `vdintlv(dst0,dst1,src0,src1)`
各发射一条 RV_VINTLV / RV_VDINTLV B32，不拆成两条单输出指令。
两个输出分别VSTS，共128个FP32元素，与交织/解交织独立golden逐字节一致。

| 指令 | PC | ID | popped-log cycle | done cycle | EXU日志执行cycle | EXU |
|---|---|---:|---:|---:|---:|---:|
| RV_VINTLV B32 | 0x13b2e10c | 52 | 1652 | 1663 | 1653 | 0 |
| RV_VDINTLV B32 | 0x13b2e10c | 52 | 1651 | 1662 | 1652 | 0 |

反汇编只打印Vd[0]，但两个store分别读取V0/V1且golden通过，不能因此截断第二个输出。
日志不能证明内部不存在更细粒度操作，也没有确认两个结果的独立写回时刻。

## 首版配置

- 支持CCE FP32，Canonical form为fp32或b32；其他位宽尚未覆盖。
- 一条COMPUTE Uop，两个vector输入、两个vector输出，均参与SSA/循环回边/rename。
  原地调用先绑定旧源，再分配两个新物理目的；IDU按两个目的扣减vector credit。
- latency=11，采用上表popped-log到done口径；若以EXU日志执行时刻计数则为10，
  不可混用两种起点。
- ALU、EXU0_ONLY为首版保守配置：只观察到EXU0，未证明EXU1不可用。
- 缺失forwarding按11-3=8兜底并warning；II沿用已有默认冲突近似，未测self-II。
  当前两目的共享Uop时序，未增加逐结果写回模型，预测精度需后续校准。
- 复用现有Python/Native多目的Core，不新增opcode调度特判。Catalog允许计算指令声明
  多个目的，具体输入输出数量仍由签名严格验证。

## 复现

```bash
python3 cce_code/predicate_select_test/run.py --probe vintlv
python3 cce_code/predicate_select_test/run.py --probe vdintlv
```

本次原始文件：`/tmp/vfsim-predicate-select-sngr9ubn/`（VINTLV）和
`/tmp/vfsim-predicate-select-ooajbp9x/`（VDINTLV），含CCE、host、commands、golden、
编译日志和core0日志。临时目录不随Git分发，上述命令可重新生成。

`tests/test_interleave.py` 覆盖两目的、原地读写、跨迭代依赖、两个输出的独立消费者、
非法签名、参数以及Python/Native逐指令发射与VF结束周期一致性。

## 到 VSTS 的 forwarding 补测

除原顺序外，新增 `--reverse-stores` 探针交换两个store的代码顺序，仍保存原来的
输出地址，两个输出golden均通过。统一以producer的EXQ ISU_ISSUE / popped-log为起点。

| 指令/顺序 | producer start | dst0 store start | dst1 store start | 间隔 |
|---|---:|---:|---:|---|
| VINTLV 正序 | 1652 | 1661 | 1662 | 9 / 10 |
| VINTLV 反序 | 1652 | 1661 | 1662 | 9 / 10 |
| VDINTLV 正序 | 1651 | 1660 | 1661 | 9 / 10 |
| VDINTLV 反序 | 1652 | 1661 | 1662 | 9 / 10 |

反序时先执行PC=0x13b2e114、ID=54、读V0的store，再执行PC=0x13b2e110、ID=53、读V1的store。
ISU_WAKEUP_READY也分别在start+9/+10，因而不能简单认为第二条仅被单store端口拖慢。
当前证据支持按输出索引区别的有效forwarding：dst0为9，dst1为10。
这仍是当前B32场景观测，不是所有消费者/位宽的完整矩阵。

现有模型pair参数按producer/consumer opcode查询，不包含producer输出索引。
按用户确认，当前将两条指令到VSTS的forwarding统一配置为9 cycles，覆盖fp32/b32
的producer/consumer组合，不区分目的索引。dst1相对实测的10可能提前1 cycle，
这是明确接受的近似；单store端口竞争仍由正常LSU仲裁处理。
到其他计算消费者仍采用原fallback，未扩展按输出索引选择forwarding的机制。

反序原始目录：`/tmp/vfsim-predicate-select-z68igrdo/`（VINTLV）、
`/tmp/vfsim-predicate-select-765rik70/`（VDINTLV）。复现：

```bash
python3 cce_code/predicate_select_test/run.py --probe vintlv --reverse-stores
python3 cce_code/predicate_select_test/run.py --probe vdintlv --reverse-stores
```
