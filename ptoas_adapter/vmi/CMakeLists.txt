# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# SPDX-License-Identifier: CANN-1.0

add_library(vfsim_vmi_adapter STATIC
  VmiUnrollPlanner.cpp
  VmiCanonicalAdapter.cpp
)
add_library(vfsim::vmi_adapter ALIAS vfsim_vmi_adapter)
target_link_libraries(vfsim_vmi_adapter PUBLIC
  vfsim::native_core
  MLIRIR
  MLIRSupport
)
target_include_directories(vfsim_vmi_adapter SYSTEM PUBLIC
  ${MLIR_INCLUDE_DIRS}
  ${LLVM_INCLUDE_DIRS}
)
