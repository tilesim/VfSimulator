# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# SPDX-License-Identifier: CANN-1.0

add_subdirectory(tileop)
add_subdirectory(vmi)

# Keep the existing PTOAS link target while exposing each adapter separately.
add_library(vfsim_ir_planner INTERFACE)
add_library(vfsim::ir_planner ALIAS vfsim_ir_planner)
target_link_libraries(vfsim_ir_planner INTERFACE
  vfsim::tileop_adapter
  vfsim::vmi_adapter
)
