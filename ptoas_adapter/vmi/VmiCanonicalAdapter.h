// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0
#pragma once
#include "api/common/schema.h"
namespace mlir {
class Operation;
}
namespace vfsim {
enum class VmiUnrollMode { NoUnroll, Abcabc, Aabbcc };
std::optional<int64_t> getStaticLoopTripCount(mlir::Operation *loop);
std::optional<CanonicalVfInfo> lowerVmiScope(mlir::Operation *vectorScope,
                                             mlir::Operation *targetLoop, VmiUnrollMode mode,
                                             int64_t factor, std::string &reason);
} // namespace vfsim
