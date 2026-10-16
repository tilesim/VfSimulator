# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# SPDX-License-Identifier: CANN-1.0

foreach(test IN ITEMS input_api address_state predicate_register membar_timing
    components parameters programmatic cce value_views)
  add_executable(vfsim_${test}_test ${test}_test.cpp)
  target_link_libraries(vfsim_${test}_test PRIVATE vfsim::native_core)
  add_test(NAME vfsim_${test}_test COMMAND vfsim_${test}_test)
endforeach()

if(VFSIM_ENABLE_MLIR_PLANNER)
  add_executable(vfsim_ptoas_adapter_test ptoas_adapter_test.cpp)
  target_link_libraries(vfsim_ptoas_adapter_test PRIVATE
    vfsim::ir_planner MLIRParser MLIRArithDialect MLIRFuncDialect MLIRSCFDialect)
  add_test(NAME vfsim_ptoas_adapter_test COMMAND vfsim_ptoas_adapter_test)
endif()
