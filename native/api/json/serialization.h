// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#ifndef VFSIM_API_NATIVE_JSON_SERIALIZATION_H
#define VFSIM_API_NATIVE_JSON_SERIALIZATION_H

#include "native/api/common/schema.h"
#include "native/api/json/value.h"

namespace vfsim {

// Language-neutral CanonicalVfInfo v1 object decoder shared by the product
// JSON adapter and Canonical input contract tests.
CanonicalVfInfo decodeCanonicalVfInfoJson(const json::Value &root);

} // namespace vfsim

#endif // VFSIM_API_NATIVE_JSON_SERIALIZATION_H
