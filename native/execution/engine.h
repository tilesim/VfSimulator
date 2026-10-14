#pragma once
#include "native/trace/writer.h"
#include "native/uarch/idu.h"
#include "native/uarch/ifu.h"
#include "native/uarch/lsu.h"
#include "native/uarch/ooo.h"
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
  std::deque<std::pair<int64_t, DynamicInst>> pipe_;
  std::map<int64_t, UopPtr> active_;
  int64_t cycle_ = 0, lastDone_ = 0, vectorCredit_, predicateCredit_,
          shqCredit_, pendingShq_ = 0, pendingLsq_ = 0;
};
} // namespace vfsim
