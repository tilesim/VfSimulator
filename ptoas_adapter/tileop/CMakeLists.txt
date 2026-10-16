# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# SPDX-License-Identifier: CANN-1.0

add_library(vfsim_tileop_adapter STATIC
  TileOpPlanner.cpp
  TileOpTemplates.cpp
)
add_library(vfsim::tileop_adapter ALIAS vfsim_tileop_adapter)
target_link_libraries(vfsim_tileop_adapter PUBLIC
  vfsim::native_core
  MLIRIR
  MLIRSupport
)
target_include_directories(vfsim_tileop_adapter SYSTEM PUBLIC
  ${MLIR_INCLUDE_DIRS}
  ${LLVM_INCLUDE_DIRS}
)
