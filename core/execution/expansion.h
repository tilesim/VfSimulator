#pragma once
#include <memory>
#include <optional>
#include "core/execution/prepare.h"
namespace vfsim {

class CanonicalCursor;
PreparedProgram expandCanonicalProgram(
    const CanonicalVfInfo &, const ParamDB &, const UarchConfig &,
    std::unordered_map<std::string, ValueInfo> values, bool retainDiagnostics,
    bool keepInstanceMetadata = true);
// LICM-aware entry point: expands a borrowed root context while the immutable
// value/storage/parameter tables stay owned by the given CanonicalVfInfo.
PreparedProgram expandCanonicalProgram(
    const CanonicalVfInfo &, const std::vector<CanonicalNode> &context,
    const ParamDB &, const UarchConfig &,
    std::unordered_map<std::string, ValueInfo> values, bool retainDiagnostics,
    bool keepInstanceMetadata = true);
// Streaming layout: builds the static layout once (loop topology, static pc map,
// lowering plan, binding slots) and hands back both the Engine metadata and the
// layout the cursor lowers from. Static metadata only: no dynamic instruction,
// operand id or instance value is materialised, and, like the eager no-trace
// preparation, it
// retains no value identity table: every streamed event carries its operand
// storage halves, so no value is ever resolved by name on this route.
//
// postLicmContext is engaged only when LICM actually rewrote the root context;
// that rewrite is then moved into the layout. Otherwise the layout borrows the
// caller's root context (vf.context) instead of copying it, which is exactly
// what the eager preparation does with the context it expands. The caller must
// keep that context alive for the layout's lifetime -- the same lifetime the
// layout already requires for the CanonicalVfInfo it lowers from.
PreparedStreamingLayout expandCanonicalStreamingLayout(
    const CanonicalVfInfo &,
    std::optional<std::vector<CanonicalNode>> postLicmContext, const ParamDB &,
    const UarchConfig &);
// Cursor over a layout produced above: it consumes the move-only handle (the
// layout is the cursor's own mutable state) instead of interning the static graph
// again. The layout is built in scoring mode, so the cursor is always the normal
// no-trace one; there is no diagnostics=true variant to expose.
std::unique_ptr<CanonicalCursor> makeCanonicalCursorForLayout(
    const CanonicalVfInfo &, CanonicalLayoutHandle, const ParamDB &,
    const UarchConfig &);
}
