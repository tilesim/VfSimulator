# Canonical 输入合同 fixture

`v2_external_predicate_forms.json` 是根据950PR报告中的外部form命名手工构造的
Canonical v2兼容性fixture，**不是TileSim/HiVM Adapter的真实导出文件**。
它直接表达MOVVP.b16 -> PSTU.b32 -> VSTAS.b32，绕过CCE parser。
普通vector live-in作为位模式输入，predicate必须由MOVVP生产；align state通过
append/consume属性表达，PSTU显式携带指针推进和打包span。

`tests/test_external_predicate_forms.py` 还覆盖B16/B32组合、uint形式等价性、
Python/Native发射与结束周期、PSTU地址合同拒绝和混合VSTAS form forwarding。
form表示指令模式，value.dtype仍描述数据值类型；不做全局b32到uint32的dtype替换。

后续拿到真实Adapter导出时应新增匿名化fixture并复测。这组测试不代表原报告中的
所有HiVM失败case已经端到端通过，也不替代Adapter对状态、地址和多结果的映射。
