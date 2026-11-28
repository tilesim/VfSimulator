#pragma once
#include <stdexcept>
#include "core/trace/writer.h"
#include "core/uarch/idu.h"
#include "core/uarch/ifu.h"
#include "core/uarch/lsu.h"
#include "core/uarch/ooo.h"
namespace vfsim {
class Engine {
public:
  Engine(const PreparedProgram &, const UarchConfig &, const ParamDB &);
  // Internal scoring-only bounded provider; default remains full eager.
  //
  // A streamed Engine is single-run by construction: the provider is detached
  // when that run ends, so a second run cannot be served. Two misuse cases are
  // refused up front instead of silently dropping work:
  //   * a program whose instructions were materialised (the stream never reads
  //     them), and
  //   * a second run on the same Engine (run() throws before touching a queue).
  void useInstructionStream(IFU::DoneFn done, IFU::FetchIntoFn fetch,
                            int32_t operandBlockSize = 0,
                            int32_t maxSlots = 0,
                            bool numericTokens = false) {
    if (!program_.instructions.empty())
      throw std::logic_error(
          "useInstructionStream requires an on-demand program: its prepared "
          "instruction vector must be empty, otherwise those instructions "
          "would be silently dropped");
    ifu_.setProvider(std::move(done), std::move(fetch));
    streamOperandBlockSize_ = operandBlockSize;
    streamNumericTokens_ = numericTokens;
    streamMaxSlots_ = maxSlots;
    // The scoring cursor hands over numeric value tokens instead of identity
    // strings; rename then keys its live-binding RAT by uint64.
    registers_.setNumericTokens(numericTokens);
    streamSingleRun_ = true;
  }
  // Detaches the on-demand provider installed by useInstructionStream and
  // leaves the single-run latch set: a streamed Engine stays spent.
  // restores the identity mode the eager program expects. The provider callbacks
  // capture caller-owned state, so every use of useInstructionStream must end
  // with this call before that state dies (runScoringStream does it in an RAII
  // guard on all exit paths).
  void clearInstructionStream() {
    ifu_.clearProvider();
    streamOperandBlockSize_ = 0;
    streamNumericTokens_ = false;
    streamMaxSlots_ = 0;
    registers_.setNumericTokens(false);
  }
  SimulationResult run(const std::filesystem::path &outDir = {},
                       int64_t maxCycles = 0);

private:
  void reclaimStreamArena();
  using Reservation = std::array<int64_t, 5>;
  Reservation reservation(const DynamicInst &) const;
  void admit(const DynamicInst &);
  bool hasPending(int64_t seq, const std::string &cls) const;
  bool hasPendingDispatch(int64_t seq) const;
  void backendStep();
  const PreparedProgram &program_;
  const UarchConfig &cfg_;
  const ParamDB &db_;
  Registers registers_;
  SHQ shq_;
  EXU exu_;
  EXQ exq_;
  TraceRecorder trace_;
  MembarController control_;
  LSU lsu_;
  LoopControl loops_;
  IDU idu_;
  OoO ooo_;
  IFU ifu_;
  std::deque<std::pair<int64_t, const DynamicInst *>> pipe_;
  std::vector<std::unique_ptr<DynamicInst>> streamSlots_;
  std::vector<size_t> streamFreeSlots_;
  size_t lastAcquiredStreamSlot_ = 0;
  int32_t streamOperandBlockSize_ = 0;
  // Mirrors useInstructionStream numericTokens: only the scored route sends
  // numeric value tokens, so only it can skip the loop scheduling history.
  bool streamNumericTokens_ = false;
  int32_t streamMaxSlots_ = 0;
  // Single-run latch of a streamed Engine: set when a provider is installed and
  // kept after detaching, so run() can refuse a second run up front.
  bool streamSingleRun_ = false;
  bool streamConsumed_ = false;
  std::deque<size_t> streamInFlight_;
  DynamicInst *acquireStreamSlot();
  // Returns the slot that was just acquired (the FIFO tail) to the free list.
  // Used for MEMBAR events, whose acceptance copies every field it needs, so no
  // pointer into the slot survives the call.
  void returnStreamSlotTail();
  // Number of admitted-but-unfinished instructions. Each of them is owned by
  // the LSU or SHQ queue (and later by EXQ/EXU), the expander allocates
  // instruction ids monotonically, and the halt test only asks whether the set
  // is empty, so a counter replaces the previous id->uop map.
  int64_t activeCount_ = 0;
  int64_t cycle_ = 0, lastDone_ = 0, vectorCredit_, predicateCredit_,
          shqCredit_, pendingShq_ = 0, pendingLsq_ = 0;
};
} // namespace vfsim
