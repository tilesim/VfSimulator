# 谓词寄存器访存

`PLDS` 从 UB 读取完整的 256-bit 谓词位图，`PSTS` 将位图写回 UB。
两条指令使用固定 `b8` form，有效数据跨度为 32 字节，由 LSU 的 load/store 端口调度。
PLDS 的目的值分配物理谓词寄存器，并参与版本化、依赖追踪和资源回收。

## 输入和地址

```c++
plds(predicate, pointer, byte_offset, NORM);
psts(predicate, pointer, byte_offset, NORM);
```

Catalog 声明 `memory_address_unit_bytes=1` 和 `memory_span_by_form.b8=32`。
Canonical 的 `MemoryAccess` 使用 `address_unit_bytes=1`、`span=32`。
输入校验检查地址单位和跨度；JSON 编解码保留这些字段。

第三个参数按字节计，允许负偏移。指针别名的 C 指针运算先按元素宽度换算：
对于 `uint32_t *slot = base + 8`，`psts(p, slot, 32, NORM)` 相对 base 的字节偏移为 64。
当前支持 `NORM` 模式。访存之间的同步由 Membar 控制。

## 时序参数

| 参数 | 周期 |
| --- | ---: |
| PLDS / PSTS latency | 9 |
| PSET_B8/B16/B32 → PSTS | 4 |
| VCMP_EQ.fp32 → PSTS | 4 |
| PLDS → VSEL.fp32 / VADDS.fp32 / PAND.b8 / VCMP_EQ.fp32 | 6 |
| PLDS → PSTS / VSTS.fp32 | 8 |

配置采用该分支记录的 A5 测量参数；未列出的 forwarding 组合通过参数回退规则查询。
LSU 调度沿用 Catalog 的 256 字节事务预算。32 字节表示有效位图跨度，
更精确的小事务吞吐率需要单独校准。

## 验证

`native/tests/predicate_register_test.cpp` 检查谓词访存、寄存器分配、时序和缺失地址单位。
`tests/test_predicate_spill.py` 检查 CCE 偏移、别名、JSON 往返、输入校验和循环资源回收。
配置 `VFSIM_NATIVE_RUNNER` 后，测试比较 Python 与 C++ 的指令开始、完成和总周期。
