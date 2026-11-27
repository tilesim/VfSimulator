// task-5 candidate driver: the real Engine fed by the canonical cursor.
//
// Modes
//   eager   identical to the task-4 frozen reference path:
//           prepareProgram -> Engine -> run  (gold reproduction in this tree)
//   stream  prepareProgramForStream(skipHoist) + makeCanonicalCursorForContext
//           -> Engine::useInstructionStream -> run. The provider pulls one
//           instruction from the cursor each time the IFU asks, so the Engine
//           schedules from a live stream; no pre-generated vector is replayed.
//           --no-preinst additionally clears the prepared instruction vector
//           before the Engine is constructed, which proves the run cannot be a
//           replay of prepare's materialised stream.
//
// Failure policy (whole-program, never a mixed stream):
//   * cursor declines / prepare-for-stream throws / the provider fails / the
//     Engine throws for a non-budget reason  -> the streamed attempt is thrown
//     away and one fresh eager run is made on a fresh Engine; the report says
//     mode=eager-fallback and keeps the original reason.
//   * a maxCycles refusal is reported as BUDGET_EXCEEDED (the refusal is the
//     contract under test, and eager would refuse identically).
//   * a trace run always uses the eager diagnostics path.
//
// Observable fields use the task-4 names so acceptance.json stays comparable.
#include "api/input_api.h"
#include "core/execution/cursor.h"
#include "core/execution/scoring_stream.h"
#include "core/execution/engine.h"
#include "core/execution/optimization.h"
#include "core/execution/prepare.h"
#include "core/params/database.h"
#include "core/params/uarch.h"

#include <cstdint>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <exception>
#include <filesystem>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

namespace {

void mix(uint64_t &h, const std::string &s) {
  for (unsigned char c : s) { h ^= c; h *= 1099511628211ull; }
  h ^= 0xffu; h *= 1099511628211ull;
}
std::string quote(const std::string &text) {
  std::string out = "'";
  for (char c : text) out += (c == '\'' || c == '\n') ? ' ' : c;
  out += '\'';
  return out;
}
void mix(uint64_t &h, int64_t v) {
  for (int i = 0; i < 8; ++i) {
    h ^= static_cast<unsigned char>((static_cast<uint64_t>(v) >> (i * 8)) & 0xff);
    h *= 1099511628211ull;
  }
}
void mixAccess(uint64_t &h, const vfsim::CanonicalMemoryAccess &a) {
  mix(h, a.baseObjectId); mix(h, a.offset.constant);
  for (const auto &t : a.offset.terms) { mix(h, t.variableId); mix(h, t.coefficient); }
  mix(h, static_cast<int64_t>(a.accessKind));
  mix(h, a.span.value_or(-1));
  mix(h, a.aliasGroup.value_or(""));
  mix(h, a.addressStateId.value_or(""));
  mix(h, a.updateMode);
  mix(h, a.addressUnitBytes.value_or(-1));
}
void mixStructure(uint64_t &h, const vfsim::DynamicInst &d) {
  mix(h, d.opText()); mix(h, d.formText()); mix(h, d.opClassText());
  mix(h, d.topBlockId); mix(h, d.streamSeq); mix(h, d.instId);
  for (const auto &s : d.src) mix(h, s);
  mix(h, static_cast<int64_t>(d.src.size()));
  for (const auto &s : d.dst) mix(h, s);
  mix(h, static_cast<int64_t>(d.dst.size()));
  for (auto v : d.iterStack) mix(h, v);
  mix(h, static_cast<int64_t>(d.iterStack.size()));
  mix(h, static_cast<int64_t>(d.isLastInTopBlock));
}
void mixLifetime(uint64_t &h, const vfsim::DynamicInst &d) {
  // The streamed lowering borrows the plan's flag store, so the digest reads the
  // same bits through the field accessors (the eager path keeps the vectors).
  const size_t srcCount =
      d.srcTokens.empty() ? d.src.size() : d.srcTokens.size();
  for (size_t i = 0; i < srcCount; ++i)
    mix(h, static_cast<int64_t>(d.srcReleased(i)));
  const size_t dstCount =
      d.dstTokens.empty() ? d.dst.size() : d.dstTokens.size();
  for (size_t i = 0; i < dstCount; ++i)
    mix(h, static_cast<int64_t>(d.dstKept(i)));
}
void mixAddress(uint64_t &h, const vfsim::DynamicInst &d) {
  for (size_t i = 0; i < d.memoryAccessCount(); ++i)
    mixAccess(h, d.memoryAccessAt(i));
}

struct Digest {
  uint64_t structure = 1469598103934665603ull;
  uint64_t lifetime = 1469598103934665603ull;
  uint64_t address = 1469598103934665603ull;
  int64_t events = 0;
  int64_t instEvents = 0;
  int64_t membarEvents = 0;
  void add(const vfsim::DynamicInst &d) {
    ++events;
    if (d.type == "membar") ++membarEvents; else ++instEvents;
    mixStructure(structure, d); mixLifetime(lifetime, d); mixAddress(address, d);
  }
  // Event accounting without the field hashing: used by timing runs so the
  // stream and the eager preparation are charged the same work.
  void skip(const vfsim::DynamicInst &d) {
    ++events;
    if (d.type == "membar") ++membarEvents; else ++instEvents;
  }
};

struct Outcome {
  std::string mode = "eager";
  std::string reason;
  int64_t cycles = -1, vfEnd = -1, linearInst = -1;
  int64_t arenaHigh = 0, issued = 0, liveHigh = 0;
  int64_t preparedStream = -1;
  int64_t prefetches = 0;
  int64_t delivered = 0;
  int64_t doneCalls = 0;
  int providerIdsApplied = 0;
  int64_t programInstructions = -1;
  int64_t prepareUs = -1, runUs = -1;
  Digest digest;
  std::string error;
  bool budgetRefused = false;
  bool ok = false;
  std::string resultsDir, tracePath;
  int traceFiles = 0;
};

// Adapter between the cursor's pull API and the IFU provider contract.
//
// CanonicalCursor::exhausted() is the internal-state emptiness predicate, not a
// "no further event will be produced" lookahead: after the last event of a loop
// the frame chain is finished but not yet unwound, so exhausted() is still false
// until one more next(). Asked before that pull, the IFU would fetch once more
// and see next()==false. A one-deep lookahead buffer makes done() exact, keeps
// the provider on-demand (one DynamicInst in flight, no stream materialization)
// and leaves the cursor API untouched. The "count" variant derives done() from
// the prepared event total instead and is used as an independent cross-check.
struct CursorProvider {
  explicit CursorProvider(vfsim::CanonicalCursor &c) : cursor(c) {}
  vfsim::CanonicalCursor &cursor;
  // Dense logical-id bridge (the operand-id contract the Engine's compact RAT
  // consumes for streamed runs): every value name gets one stable id, written
  // into the per-slot block of PreparedProgram::operandIds that the Engine
  // hands over in DynamicInst::srcIdBase. Identity comes from the cursor's
  // names, not from any prepared instruction copy.
  bool noDigest = false;
  bool useIds = false;
  std::vector<int32_t> *table = nullptr;
  int32_t blockSize = 0;
  std::unordered_map<std::string, int32_t> idOf;
  int32_t nextId = 0;
  int32_t idFor(const std::string &name) {
    auto it = idOf.find(name);
    if (it != idOf.end()) return it->second;
    const int32_t id = nextId++;
    idOf.emplace(name, id);
    return id;
  }
  int64_t expected = -1;      // prepared event total, only for doneMode=count
  bool useCount = false;
  // Three objects rotate (arena slot <-> buffer <-> scratch) so the lowering
  // always finds spare capacity; no per-event allocation after warm-up.
  vfsim::DynamicInst buffer;
  vfsim::DynamicInst scratch;
  bool have = false;
  bool ended = false;
  int64_t delivered = 0;
  int64_t prefetches = 0;
  int64_t doneCalls = 0;
  Digest digest;
  std::string error;

  int64_t cursorNs = 0;
  void prefetch() {
    if (have || ended) return;
    ++prefetches;
    const auto start = std::chrono::steady_clock::now();
    const bool got = cursor.next(scratch);
    cursorNs += std::chrono::duration_cast<std::chrono::nanoseconds>(
                    std::chrono::steady_clock::now() - start)
                    .count();
    if (got) {
      std::swap(buffer, scratch);
      have = true;
    } else {
      ended = true;
    }
  }
  bool done() {
    ++doneCalls;
    if (useCount) return delivered >= expected;
    prefetch();
    return !have;
  }
  void fetch(vfsim::DynamicInst &out) {
    if (useCount && delivered >= expected)
      throw std::runtime_error("provider fetch past the prepared event total");
    // The Engine hands the slot's operand block over in srcIdBase; capture it
    // before the cursor overwrites the whole DynamicInst.
    const int32_t slotBase = out.srcIdBase;
    if (!useCount) {
      if (!have) {
        error = "provider fetch without a buffered instruction";
        throw std::runtime_error(error);
      }
      std::swap(out, buffer);
      have = false;
    } else if (!cursor.next(out)) {
      error = "cursor stream ended before the prepared event total";
      throw std::runtime_error(error);
    }
    if (useIds && table != nullptr) {
      const int32_t base = slotBase;
      if (base < 0 || blockSize <= 0) {
        error = "stream slot exposes no operand block";
        throw std::runtime_error(error);
      }
      const size_t need = static_cast<size_t>(base) + out.src.size() + out.dst.size();
      if (need > table->size()) {
        error = "stream operand id table overflow";
        throw std::runtime_error(error);
      }
      for (size_t i = 0; i < out.src.size(); ++i)
        (*table)[static_cast<size_t>(base) + i] = idFor(out.src[i]);
      const size_t dstBase = static_cast<size_t>(base) + out.src.size();
      for (size_t i = 0; i < out.dst.size(); ++i)
        (*table)[dstBase + i] = idFor(out.dst[i]);
      out.srcIdBase = base;
      out.dstIdBase = static_cast<int32_t>(dstBase);
    }
    ++delivered;
    if (noDigest)
      digest.skip(out);
    else
      digest.add(out);
    if (!useCount) prefetch();
  }
};

// Bounded stream slot pool (same bound the frozen stream prototype used).
constexpr int32_t kStreamMaxSlots = 64;

bool isBudgetRefusal(const std::string &text) {
  return text.find("did not complete before max_cycles") != std::string::npos;
}

// One fresh eager run; always available, so any degraded stream attempt still
// ends in a complete whole-program execution.
Outcome runEager(const vfsim::CanonicalVfInfo &vf, vfsim::ParamDB &db,
                 vfsim::UarchConfig &cfg, bool retainsDiagnostics,
                 const std::string &traceDir, int64_t maxCycles,
                 bool forceStringRat = false, bool noDigest = false) {
  Outcome out;
  out.mode = retainsDiagnostics ? "eager-diag" : "eager";
  const auto prepareStart = std::chrono::steady_clock::now();
  vfsim::PreparedProgram program = vfsim::prepareProgram(vf, db, cfg, retainsDiagnostics);
  out.prepareUs = std::chrono::duration_cast<std::chrono::microseconds>(
                      std::chrono::steady_clock::now() - prepareStart)
                      .count();
  // Test-only switch: dropping the dense operand table selects the string RAT,
  // the mode a name-based stream provider runs in. Used to show the two RAT
  // modes do not change the schedule.
  if (forceStringRat) {
    program.operandIds.clear();
    program.operandOwner.clear();
  }
  out.linearInst = program.linearInstCount;
  for (const auto &d : program.instructions)
    if (noDigest) out.digest.skip(d); else out.digest.add(d);
  out.preparedStream = out.digest.events;
  vfsim::Engine engine(program, cfg, db);
  try {
    const auto runStart = std::chrono::steady_clock::now();
    const auto result = engine.run(traceDir, maxCycles);
    out.runUs = std::chrono::duration_cast<std::chrono::microseconds>(
                    std::chrono::steady_clock::now() - runStart)
                    .count();
    out.ok = true;
    out.cycles = result.cyclesExecuted;
    out.vfEnd = result.vfEndCycle;
    out.linearInst = result.linearInstCount;
    out.resultsDir = result.resultsDir;
    out.tracePath = result.tracePath;
    if (!traceDir.empty()) {
      std::error_code ec;
      out.traceFiles = std::filesystem::exists(
          std::filesystem::path(traceDir) / "trace.json", ec) ? 1 : 0;
    }
  } catch (const std::exception &e) {
    out.error = e.what();
    out.budgetRefused = isBudgetRefusal(out.error);
  }
  out.arenaHigh = engine.streamArenaHighWater();
  out.issued = engine.streamIssuedInstructions();
  out.liveHigh = engine.streamLiveValueHighWater();
  return out;
}

}  // namespace

int main(int argc, char **argv) {
  if (argc < 2) {
    std::fprintf(stderr,
                 "usage: %s <fixture.json> [--mode eager|stream] [--max-cycles N]\n"
                 "          [--dyn-limit N] [--trace-dir DIR] [--no-preinst]\n"
                 "          [--expect-budget-exceeded]\n",
                 argv[0]);
    return 2;
  }
  const std::string fixture = argv[1];
  std::string mode = "eager", traceDir;
  bool cursorDiag = true, cursorDiagForced = false;
  int64_t maxCycles = 1000000, dynLimit = 2000000;
  bool noPreinst = false, expectBudget = false, forceStringRat = false;
  bool keepTextMetadata = true;
  std::string providerDone = "buffered";
  bool providerIds = false;
  bool diagEager = false;
  bool noDigest = false;
  int64_t expectEvents = -1;
  for (int i = 2; i < argc; ++i) {
    const std::string key = argv[i];
    if (key == "--cursor-diag") {
      cursorDiag = true;
      cursorDiagForced = true;
    }
    else if (key == "--mode" && i + 1 < argc) mode = argv[++i];
    else if (key == "--max-cycles" && i + 1 < argc) maxCycles = std::stoll(argv[++i]);
    else if (key == "--dyn-limit" && i + 1 < argc) dynLimit = std::stoll(argv[++i]);
    else if (key == "--trace-dir" && i + 1 < argc) traceDir = argv[++i];
    else if (key == "--no-preinst") noPreinst = true;
    else if (key == "--expect-budget-exceeded") expectBudget = true;
    else if (key == "--force-string-rat") forceStringRat = true;
    else if (key == "--provider-done" && i + 1 < argc) providerDone = argv[++i];
    else if (key == "--no-text-metadata") keepTextMetadata = false;
    else if (key == "--provider-ids") providerIds = true;
    else if (key == "--diag") diagEager = true;
    else if (key == "--no-digest") noDigest = true;
    else if (key == "--expect-events" && i + 1 < argc)
      expectEvents = std::stoll(argv[++i]);
    else { std::fprintf(stderr, "unknown arg: %s\n", key.c_str()); return 2; }
  }
  if (mode != "eager" && mode != "stream" && mode != "stream-static" &&
      mode != "scoring") {
    std::fprintf(stderr, "unknown mode: %s\n", mode.c_str());
    return 2;
  }
  const bool staticPrepare = mode == "stream-static";
  // The materialising seam keeps diagnostic lowering so its 15-field oracle
  // digests stay comparable with the diagnostic eager preparation. The scoring
  // path lowers compactly; --cursor-diag forces the diagnostic lowering there.
  if (staticPrepare && !cursorDiagForced)
    cursorDiag = false;
  vfsim::CanonicalVfInfo vf;
  try {
    vf = vfsim::InputAPI::loadJson(fixture);
  } catch (const std::exception &e) {
    std::printf("ENGINE_RESULT fixture=%s mode=%s status=LOAD_FAIL error=%s\n",
                fixture.c_str(), mode.c_str(), quote(e.what()).c_str());
    return 3;
  }
  const std::filesystem::path root(VFSIM_SOURCE_ROOT);
  vfsim::ParamDB db(root);
  auto cfg = db.uarch();
  cfg.canonicalDynamicInstructionLimit = dynLimit;
  // --diag keeps the diagnostic preparation so the eager stream carries the same
  // value-spelling namespace as the cursor (headless preparation compacts names,
  // which changes the digest namespace only, not the schedule).
  const bool retainsDiagnostics = !traceDir.empty() || diagEager;

  // Traces and explicit eager mode keep the frozen diagnostics path untouched.
  if (mode == "eager" || !traceDir.empty()) {
    const bool traced = !traceDir.empty();
    const std::string eagerMode = traced ? "eager-trace"
                                         : (diagEager ? "eager-diag" : "eager");
    Outcome out;
    try {
      out = runEager(vf, db, cfg, retainsDiagnostics, traceDir, maxCycles,
                     forceStringRat, noDigest);
    } catch (const std::exception &e) {
      std::printf("ENGINE_RESULT fixture=%s mode=%s dyn_limit=%lld status=PREPARE_FAIL error=%s\n",
                  fixture.c_str(), eagerMode.c_str(),
                  (long long)dynLimit, quote(e.what()).c_str());
      return 4;
    }
    const char *status = out.ok ? "OK"
                                : (out.budgetRefused ? "BUDGET_EXCEEDED" : "ENGINE_FAIL");
    std::printf("ENGINE_RESULT fixture=%s mode=%s dyn_limit=%lld max_cycles=%lld "
                "trace=%s status=%s events=%lld inst_events=%lld membar_events=%lld "
                "cycles=%lld vf_end=%lld linear_inst=%lld digest_struct=%llu "
                "digest_life=%llu digest_addr=%llu arena_high=%lld issued=%lld "
                "live_high=%lld prepared_events=%lld fetched_events=%lld "
                "prepare_us=%lld run_us=%lld "
                "results_dir=%s trace_path=%s trace_files=%d error=%s\n",
                fixture.c_str(), eagerMode.c_str(),
                (long long)dynLimit, (long long)maxCycles,
                traceDir.empty() ? "none" : traceDir.c_str(), status,
                (long long)out.digest.events, (long long)out.digest.instEvents,
                (long long)out.digest.membarEvents, (long long)out.cycles,
                (long long)out.vfEnd, (long long)out.linearInst,
                (unsigned long long)out.digest.structure,
                (unsigned long long)out.digest.lifetime,
                (unsigned long long)out.digest.address, (long long)out.arenaHigh,
                (long long)out.issued, (long long)out.liveHigh,
                (long long)out.preparedStream, (long long)out.digest.events,
                (long long)out.prepareUs, (long long)out.runUs,
                out.resultsDir.empty() ? "-" : out.resultsDir.c_str(),
                out.tracePath.empty() ? "-" : out.tracePath.c_str(),
                out.traceFiles, quote(out.error).c_str());
    if (!out.ok) {
      if (expectBudget) return out.budgetRefused ? 0 : 8;
      return out.budgetRefused ? 5 : 6;
    }
    if (expectBudget) return 8;
    return 0;
  }

  // ---- scoring mode (library seam) -------------------------------------------
  // One call to the normal no-trace scoring entry point: static metadata +
  // cursor + on-demand provider, no lookahead buffer.
  if (mode == "scoring") {
    Outcome out;
    out.mode = "scoring";
    const auto prepareStart = std::chrono::steady_clock::now();
    vfsim::ScoringStream plan;
    try {
      plan = vfsim::prepareScoringStream(vf, db, cfg);
    } catch (const std::exception &e) {
      // Same rejection label the eager preparation uses for an unsupported
      // shape, so error fixtures compare equal.
      std::printf("ENGINE_RESULT fixture=%s mode=scoring dyn_limit=%lld "
                  "status=PREPARE_FAIL error=%s\n",
                  fixture.c_str(), (long long)dynLimit, quote(e.what()).c_str());
      return 4;
    }
    out.prepareUs = std::chrono::duration_cast<std::chrono::microseconds>(
                        std::chrono::steady_clock::now() - prepareStart)
                        .count();
    out.preparedStream = static_cast<int64_t>(plan.program.instructions.size());
    const bool cleanStatic = plan.program.instructions.empty() &&
                             plan.program.operandIds.empty() &&
                             plan.program.operandOwner.empty();
    if (plan.declineReason != nullptr && *plan.declineReason != '\0') {
      // The cursor refuses this shape; the eager preparation may still accept
      // it, so degrade exactly like the materialising seam does.
      Outcome fallback;
      try {
        fallback =
            runEager(vf, db, cfg, false, {}, maxCycles, forceStringRat, noDigest);
      } catch (const std::exception &e) {
        std::printf("ENGINE_RESULT fixture=%s mode=eager-fallback "
                    "status=PREPARE_FAIL error=%s\n",
                    fixture.c_str(), quote(e.what()).c_str());
        return 4;
      }
      fallback.mode = "eager-fallback";
      fallback.reason = std::string("cursor-declined: ") + plan.declineReason;
      std::printf("ENGINE_RESULT fixture=%s mode=eager-fallback status=%s "
                  "reason='%s' cycles=%lld vf_end=%lld events=%lld\n",
                  fixture.c_str(), fallback.ok ? "OK" : "PREPARE_FAIL",
                  quote(fallback.reason).c_str(), (long long)fallback.cycles,
                  (long long)fallback.vfEnd,
                  (long long)fallback.digest.events);
      return fallback.ok ? 0 : 4;
    }
    vfsim::Engine engine(plan.program, cfg, db);
    try {
      const auto runStart = std::chrono::steady_clock::now();
      vfsim::SimulationResult result =
          vfsim::runScoringStream(plan, engine, maxCycles);
      out.runUs = std::chrono::duration_cast<std::chrono::microseconds>(
                      std::chrono::steady_clock::now() - runStart)
                      .count();
      out.ok = true;
      out.cycles = result.cyclesExecuted;
      out.vfEnd = result.vfEndCycle;
      out.linearInst = result.linearInstCount;
      out.arenaHigh = engine.streamArenaHighWater();
      out.issued = engine.streamIssuedInstructions();
      out.liveHigh = engine.streamLiveValueHighWater();
      out.digest.events = plan.fetched;
      out.digest.instEvents = plan.fetched;
      std::printf("ENGINE_RESULT fixture=%s mode=scoring dyn_limit=%lld "
                  "max_cycles=%lld trace=none status=OK events=%lld "
                  "inst_events=%lld membar_events=0 cycles=%lld vf_end=%lld "
                  "linear_inst=%lld static_event_count=%lld fetched=%lld "
                  "prefetches=%lld arena_high=%lld issued=%lld live_high=%lld "
                  "program_instructions=%lld clean_static=%d "
                  "prepare_us=%lld run_us=%lld\n",
                  fixture.c_str(), (long long)dynLimit, (long long)maxCycles,
                  (long long)plan.fetched, (long long)plan.fetched,
                  (long long)out.cycles, (long long)out.vfEnd,
                  (long long)out.linearInst, (long long)plan.eventCount,
                  (long long)plan.fetched, (long long)plan.prefetches,
                  (long long)out.arenaHigh, (long long)out.issued,
                  (long long)out.liveHigh, (long long)out.preparedStream,
                  (int)cleanStatic, (long long)out.prepareUs,
                  (long long)out.runUs);
    } catch (const std::exception &e) {
      std::printf("ENGINE_RESULT fixture=%s mode=scoring status=FAIL error=%s\n",
                  fixture.c_str(), quote(e.what()).c_str());
      return 6;
    }
    return 0;
  }

  // ---- stream mode (headless only) -------------------------------------------
  std::string decline;
  Outcome streamed;
  streamed.mode = "stream";
  int64_t preparedStream = -1;
  int64_t staticValues = -1, staticTopBlocks = -1, staticOperandIds = -1;
  int64_t streamedProgramInstructions = -1;
  (void)streamedProgramInstructions;
  std::unique_ptr<CursorProvider> provider;
  bool attempted = false;
  bool providerFailed = false;
  std::string providerError;
  try {
    // One LICM for the whole scope: the program metadata and the cursor plan
    // share this exact post-LICM context (skipHoist=true below).
    const auto prepareStart = std::chrono::steady_clock::now();
    vfsim::CompilerOptimizationStats stats;
    auto hoisted = vfsim::hoistLoopInvariantContext(vf, stats);
    std::vector<vfsim::CanonicalNode> context = hoisted ? *hoisted : vf.context;
    // stream       : task-5 acceptance seam (dynamic metadata materialisation).
    // stream-static: task-7 normal no-trace scoring preparation; nothing in the
    //                program grows with the dynamic instruction count.
    vfsim::PreparedProgram program =
        staticPrepare
            ? vfsim::prepareProgramForStreaming(vf, context, db, cfg, stats)
            : vfsim::prepareProgramForStream(vf, context, db, cfg,
                                             keepTextMetadata,
                                             /*skipHoist=*/true);
    const auto prepareEnd = std::chrono::steady_clock::now();
    streamed.prepareUs = std::chrono::duration_cast<std::chrono::microseconds>(
                             prepareEnd - prepareStart)
                             .count();
    preparedStream = static_cast<int64_t>(program.instructions.size());
    staticValues = static_cast<int64_t>(program.values.size());
    staticTopBlocks = program.totalTopBlocks;
    staticOperandIds = static_cast<int64_t>(program.operandIds.size());
    streamedProgramInstructions = static_cast<int64_t>(program.instructions.size());
    streamed.programInstructions = streamedProgramInstructions;
    if (staticPrepare && (!program.instructions.empty() ||
                          !program.operandIds.empty() ||
                          !program.operandOwner.empty())) {
      decline = "static-prepare-materialised-dynamic-metadata";
    }
    // Identity bridge for the Engine's compact RAT: default off selects the
    // string RAT (name identity); --provider-ids writes dense ids into the
    // per-slot operand block the Engine hands to the provider, exactly the
    // contract Engine::useInstructionStream documents.
    int32_t operandBlockSize = 0;
    if (providerIds) {
      size_t widest = 0;
      for (const auto &d : program.instructions)
        widest = std::max(widest, d.src.size() + d.dst.size());
      operandBlockSize = static_cast<int32_t>(widest);
      if (operandBlockSize > 0) {
        // The Engine hands each stream slot a block of this width, and its slot
        // pool is bounded by the maxSlots argument below, so the table must span
        // the whole pool (same sizing rule as the frozen stream prototype).
        program.operandIds.assign(
            static_cast<size_t>(kStreamMaxSlots) *
                static_cast<size_t>(operandBlockSize), 0);
      }
    } else {
      program.operandIds.clear();
      program.operandOwner.clear();
    }
    if (noPreinst) program.instructions.clear();
    auto cursor = vfsim::makeCanonicalCursorForContext(
        vf, std::vector<vfsim::CanonicalNode>(context), db, cfg, cursorDiag);
    const char *why = cursor->declineReason();
    if (why != nullptr && *why != '\0') {
      decline = why;
    } else {
      attempted = true;
      provider = std::make_unique<CursorProvider>(*cursor);
      provider->useCount = (providerDone == "count");
      provider->expected = expectEvents >= 0 ? expectEvents : preparedStream;
      provider->useIds = providerIds && operandBlockSize > 0;
      streamed.providerIdsApplied = provider->useIds ? 1 : 0;
      provider->table = &program.operandIds;
      provider->blockSize = operandBlockSize;
      vfsim::Engine engine(program, cfg, db);
      engine.useInstructionStream(
          [&provider]() -> bool { return provider->done(); },
          [&provider](vfsim::DynamicInst &out) { provider->fetch(out); },
          provider->useIds ? operandBlockSize : 0,
          provider->useIds ? kStreamMaxSlots : 0);
      vfsim::SimulationResult result;
      bool ran = false;
      try {
        const auto runStart = std::chrono::steady_clock::now();
        result = engine.run({}, maxCycles);
        streamed.runUs = std::chrono::duration_cast<std::chrono::microseconds>(
                             std::chrono::steady_clock::now() - runStart)
                             .count();
        ran = true;
      } catch (const std::exception &e) {
        decline = std::string("stream-error: ") + e.what();
      }
      streamed.arenaHigh = engine.streamArenaHighWater();
      streamed.issued = engine.streamIssuedInstructions();
      streamed.liveHigh = engine.streamLiveValueHighWater();
      streamed.digest = provider->digest;
      streamed.prefetches = provider->prefetches;
      streamed.delivered = provider->delivered;
      streamed.doneCalls = provider->doneCalls;
      if (ran) {
        streamed.ok = true;
        streamed.cycles = result.cyclesExecuted;
        streamed.vfEnd = result.vfEndCycle;
        streamed.linearInst = result.linearInstCount;
      }
      streamed.prefetches = provider->prefetches;
      streamed.delivered = provider->delivered;
      streamed.doneCalls = provider->doneCalls;
    }
  } catch (const std::exception &e) {
    decline = std::string("stream-error: ") + e.what();
  }
  streamed.preparedStream = preparedStream;
  if (provider) {
    streamed.prefetches = provider->prefetches;
    streamed.delivered = provider->delivered;
    streamed.doneCalls = provider->doneCalls;
    if (!streamed.ok) streamed.digest = provider->digest;
  }
  if (staticPrepare) preparedStream = streamed.digest.events;  // static path: no prepared count
  if (streamed.ok && streamed.digest.events != preparedStream) {
    streamed.ok = false;
    decline = "stream-fetch-count-mismatch";
  }
  if (streamed.budgetRefused || (streamed.ok == false && isBudgetRefusal(decline))) {
    // A budget refusal is the contract under test, not a capability gap.
    std::printf("ENGINE_RESULT fixture=%s mode=stream dyn_limit=%lld max_cycles=%lld "
                "status=BUDGET_EXCEEDED events=%lld inst_events=%lld membar_events=%lld "
                "cycles=-1 vf_end=-1 linear_inst=-1 digest_struct=0 digest_life=0 "
                "digest_addr=0 arena_high=%lld issued=%lld live_high=%lld "
                "prepared_events=%lld fetched_events=%lld results_dir=- trace_path=- "
                "trace_files=0 done_calls=%lld prefetches=%lld delivered=%lld "
                "error=%s\n",
                fixture.c_str(), (long long)dynLimit, (long long)maxCycles,
                (long long)streamed.digest.events, (long long)streamed.digest.instEvents,
                (long long)streamed.digest.membarEvents, (long long)streamed.arenaHigh,
                (long long)streamed.issued, (long long)streamed.liveHigh,
                (long long)preparedStream, (long long)streamed.digest.events,
                (long long)streamed.doneCalls, (long long)streamed.prefetches,
                (long long)streamed.delivered, quote(decline).c_str());
    if (expectBudget) return 0;
    return 5;
  }
  if (streamed.ok) {
    std::printf("ENGINE_RESULT fixture=%s mode=stream dyn_limit=%lld max_cycles=%lld "
                "trace=none status=OK events=%lld inst_events=%lld membar_events=%lld "
                "cycles=%lld vf_end=%lld linear_inst=%lld digest_struct=%llu "
                "digest_life=%llu digest_addr=%llu arena_high=%lld issued=%lld "
                "live_high=%lld prepared_events=%lld fetched_events=%lld "
                "results_dir=- trace_path=- trace_files=0 error='' "
                "provider_done=%s prefetch_depth=1 prefetches=%lld provider_ids=%d "
                "static_prepare=%d static_values=%lld static_top_blocks=%lld "
                "static_operand_ids=%lld program_instructions=%lld cursor_diag=%d "
                "prepare_us=%lld run_us=%lld cursor_ns=%lld\n",
                fixture.c_str(), (long long)dynLimit, (long long)maxCycles,
                (long long)streamed.digest.events, (long long)streamed.digest.instEvents,
                (long long)streamed.digest.membarEvents, (long long)streamed.cycles,
                (long long)streamed.vfEnd, (long long)streamed.linearInst,
                (unsigned long long)streamed.digest.structure,
                (unsigned long long)streamed.digest.lifetime,
                (unsigned long long)streamed.digest.address,
                (long long)streamed.arenaHigh, (long long)streamed.issued,
                (long long)streamed.liveHigh, (long long)preparedStream,
                (long long)streamed.digest.events, providerDone.c_str(),
                (long long)streamed.prefetches, (int)streamed.providerIdsApplied,
                (int)staticPrepare, (long long)staticValues,
                (long long)staticTopBlocks, (long long)staticOperandIds,
                (long long)streamed.programInstructions, (int)cursorDiag,
                (long long)streamed.prepareUs, (long long)streamed.runUs,
                (long long)(provider ? provider->cursorNs : 0));
    if (expectBudget) return 8;
    return 0;
  }
  // Whole-program eager fallback: the streamed attempt above is discarded and a
  // fresh Engine executes the complete program eagerly.
  (void)attempted;
  (void)providerFailed;
  Outcome fallback;
  try {
    fallback = runEager(vf, db, cfg, false, {}, maxCycles);
  } catch (const std::exception &e) {
    std::printf("ENGINE_RESULT fixture=%s mode=eager-fallback dyn_limit=%lld "
                "status=PREPARE_FAIL reason=%s error=%s\n",
                fixture.c_str(), (long long)dynLimit, quote(decline).c_str(),
                quote(e.what()).c_str());
    return 4;
  }
  std::printf("ENGINE_RESULT fixture=%s mode=eager-fallback dyn_limit=%lld max_cycles=%lld "
              "trace=none status=%s reason=%s events=%lld inst_events=%lld "
              "membar_events=%lld cycles=%lld vf_end=%lld linear_inst=%lld "
              "digest_struct=%llu digest_life=%llu digest_addr=%llu arena_high=%lld "
              "issued=%lld live_high=%lld prepared_events=%lld fetched_events=%lld "
              "results_dir=- trace_path=- trace_files=0 error=%s\n",
              fixture.c_str(), (long long)dynLimit, (long long)maxCycles,
              fallback.ok ? "OK" : (fallback.budgetRefused ? "BUDGET_EXCEEDED"
                                                          : "ENGINE_FAIL"),
              quote(decline).c_str(), (long long)fallback.digest.events,
              (long long)fallback.digest.instEvents, (long long)fallback.digest.membarEvents,
              (long long)fallback.cycles, (long long)fallback.vfEnd,
              (long long)fallback.linearInst,
              (unsigned long long)fallback.digest.structure,
              (unsigned long long)fallback.digest.lifetime,
              (unsigned long long)fallback.digest.address,
              (long long)fallback.arenaHigh, (long long)fallback.issued,
              (long long)fallback.liveHigh, (long long)fallback.preparedStream,
              (long long)fallback.digest.events, quote(fallback.error).c_str());
  if (!fallback.ok) {
    if (expectBudget) return fallback.budgetRefused ? 0 : 8;
    return fallback.budgetRefused ? 5 : 6;
  }
  if (expectBudget) return 8;
  return 0;
}
