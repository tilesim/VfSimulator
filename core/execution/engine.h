#pragma once
#include "core/trace/writer.h"
#include "core/uarch/idu.h"
#include "core/uarch/ifu.h"
#include "core/uarch/lsu.h"
#include "core/uarch/ooo.h"
namespace vfsim {
class Engine {
public:
  Engine(const PreparedProgram &, const UarchConfig &, const ParamDB &);
  SimulationResult run(const std::filesystem::path &outDir = {},
                       int64_t maxCycles = 0);

private:
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
  // Number of admitted-but-unfinished instructions. Each of them is owned by
  // the LSU or SHQ queue (and later by EXQ/EXU), the expander allocates
  // instruction ids monotonically, and the halt test only asks whether the set
  // is empty, so a counter replaces the previous id->uop map.
  int64_t activeCount_ = 0;
  int64_t cycle_ = 0, lastDone_ = 0, vectorCredit_, predicateCredit_,
          shqCredit_, pendingShq_ = 0, pendingLsq_ = 0;
};
} // namespace vfsim
