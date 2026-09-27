# A5 搬运指令首批实测

## 范围

依据用户提供的《950PR HiVM adapter修复与精度复测报告_20260920》6.1节及
《VFSim_剩余失败case搬运指令支持清单》，新增重点为VLDSX2、VLDAS、VLDUS、PSTU。
均为VF内UB访问，不是GM/UB的MTE指令。

本次完成CAModel探针和语义/时序证据，不代表Canonical/Core已完整支持这四条指令。
不通过新增一个LOAD/STORE分类就声称完成多结果、状态链、指针递推建模。
其他新测计算指令的缺失forwarding可暂用latency-3；PSET仍按前次约定取2。
状态链的就绪时间不能直接用普通向量RAW公式替代。

环境：CANN 9.0.0-beta.1，ccec dav-c310-vec，Ascend950PR_9599；
关闭VF fusion，vec-misched=0。全部成功样本均有独立golden并逐字节比对。

## 结果

| 探针 | 数据/模式 | 访问起点 | 次数 | VF cycles | 指令发射到完成 | 校验 |
|---|---|---:|---:|---:|---|---|
| VLDSX2 | FP32 / DINTLV_B32 | 0 B | 1 | 51 | 9 | 两个输出均通过 |
| VLDSX2 | FP32 / DINTLV_B32 | 32 B | 1 | 51 | 9 | 两个输出均通过 |
| VLDAS -> VLDUS | FP32 | 4 B | 3 | 55 | 各9 | 768 B通过 |
| VLDAS -> VLDUS | FP16 | 30 B | 3 | 55 | 各9 | 768 B通过 |
| VLDAS -> VLDUS | S32 | 28 B | 3 | 55 | 各9 | 768 B通过 |
| PSTU -> VSTAS | B32 | 0 B | 1 | 48 | 8；flush 9 | 写入及哨兵通过 |
| PSTU -> VSTAS | B32 | 0 B | 5 | 52 | 8/9；flush 9 | 写入及哨兵通过 |
| PSTU -> VSTAS | B32 | 8 B | 5 | 52 | 8/9；flush 9 | 写入及哨兵通过 |
| PSTU -> VSTAS | B16 | 16 B | 3 | 50 | 8/9；flush 8 | 写入及哨兵通过 |

另有B32起点4 B的负例，CAModel在PSTU处拒绝：
`tpl_pstu: Assertion dst_addr % write_size == 0 failed`。
该例没有完成数值校验，不能算通过。

## 已确认的语义

### VLDSX2

CCE接口是 `vlds(dst0, dst1, base, offset, DINTLV_B32)`，不是名为vldsx2的函数。
当前模式从128个连续FP32（512 B）解交织到两个64-lane向量，分别为偶数和奇数位置。
两个结果各写出256 B并分别参与golden，不能截掉第二个结果。
实际RV是单条 `RV_VLDI dist:DINTLV_B32`，不是两条普通VLDS。
不能由单条RV就推定只占一个寄存器写口或单个load带宽单位，这些资源占用还需独立测量。

### VLDAS / VLDUS

`vldas(state, ptr)` 生成加载侧Uld状态。
`vldus(value, state, ptr, inc, POST_UPDATE)` 同时更新state和ptr。
头文件明确inc按元素计，编译器乘sizeof(pointer element)。
本次FP16 inc=128、FP32/S32 inc=64，实际都为256 B。
RV对应 `RV_VLDAS` 和 `RV_VLDUI #offset=256`。
FP32样本指针依次为0x4、0x104、0x204，FP16为0x1e、0x11e、0x21e；
每轮读出的逻辑payload为256 B，三轮的数据连续性已验证。

VLDAS耗时9不是“i8数据计算”；它是LOAD路径的对齐状态产生。
VLDAS实际触及哪些底层UB块、VLDUS与缓存状态合并的实际事务范围，不能只凭输出推断。
本次VLDUS相邻发射1 cycle，状态可流水，不应强制每次等前一条done后才继续。

### PSTU

接口 `pstu(state, predicate, ptr)`：state和ptr都为输入输出，随后由VSTAS flush。
与此前“VSTUS只追加状态”的近似不能不经验证直接等同。

- B32：每次打包64个有效谓词位，即8 B，ptr递增8 B。
- B16：每次打包128位，即16 B，ptr递增16 B。
- PAT_VL32样本在每个8/16 B片段中仅低32 bit为1，其余为0。
- B32五次共40 B：最终flush日志ptr=0x4028；偏移8 B则为0x4030。
- B16从0x4010开始三次共48 B，最终ptr=0x4040。
- 输出区预置0xA5，所有非目标字节保持不变，已验证未扩大为256 B覆盖。

B32起址0的第四条PSTU发射到完成为9，其余为8；起址8 B时第三条为9。
这与填满32 B边界的位置相符，但目前只是相关性，不能确认为唯一成因。
B16样本也出现8/9，VSTAS flush出现8/9；不建议立刻写一个“实测固定8”的统一参数。
观测相邻PSTU可每cycle发射，flush在末条PSTU发射后1 cycle发射，未等待其done。
这只是当前链路样本，尚不是完整的端口冲突或self-II矩阵。

## 复现

```bash
python3 cce_code/predicate_select_test/memory_probe.py --kind vldsx2 --offset 0
python3 cce_code/predicate_select_test/memory_probe.py --kind vldsx2 --offset 8
python3 cce_code/predicate_select_test/memory_probe.py --kind vldus --dtype fp32 --offset 1
python3 cce_code/predicate_select_test/memory_probe.py --kind vldus --dtype fp16 --offset 15
python3 cce_code/predicate_select_test/memory_probe.py --kind vldus --dtype int32 --offset 7
python3 cce_code/predicate_select_test/memory_probe.py --kind pstu --offset 0 --iterations 1
python3 cce_code/predicate_select_test/memory_probe.py --kind pstu --offset 0 --iterations 5
python3 cce_code/predicate_select_test/memory_probe.py --kind pstu --offset 2 --iterations 5
python3 cce_code/predicate_select_test/memory_probe.py --kind pstu --dtype fp16 --offset 8 --iterations 3
```

每次输出独立/tmp目录，含生成CCE、host、commands、golden/output、完整日志和validation.json。
参数offset是源/目的指针的元素偏移，不是字节数；PSTU的fp32/fp16选项仅选择B32/B16宽度，
其实际指针是uint32_t/uint16_t，没有浮点数值转换。
归档脚本 `archive_memory.py <目录...> --out <新目录>` 保留core0 LSU/ISU/IDU、
instr_log/instr_popped_log、失败日志和可复现源码，排除空闲核及构建产物。
归档位置：`results/lsu_instruction_benchmark_20260924/`。

## 实现前仍需完成

1. Catalog和Canonical：LOAD双向量结果、Uld状态的递推、PSTU谓词数据输入及Ust状态。
2. 动态pointer state必须与alignment state区分；按真实dtype、模式计算字节范围。
3. IDU双目的vector credit原子扣账；状态不得混入普通vector/predicate物理池。
4. 测试VLDAS->多轮VLDUS、两个独立状态交错、PSTU跨块与flush、循环外消费/零次循环。
5. 校准PSTU边界相关完成差异及VLDSX2资源占用，再决定固定近似还是按状态建模。
6. TileSim的PSTU/VLDUS返回pointer提取仍需对方同步，本仓benchmark不能修复外部adapter。
