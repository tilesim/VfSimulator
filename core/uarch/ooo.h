#pragma once
#include "core/trace/recorder.h"
#include "core/uarch/exq.h"
#include "core/uarch/shq.h"
namespace vfsim {
class OoO {
public:
  OoO(const UarchConfig &cfg, Registers &regs, SHQ &shq, EXQ &exq, EXU &exu,
      TraceRecorder &trace)
      : cfg_(cfg), regs_(regs), shq_(shq), exq_(exq), exu_(exu), trace_(trace) {
  }
  void dispatch(int64_t cycle);
  void issueDirect(int64_t cycle);

private:
  int selectRoundRobin(const std::vector<int> &candidates, int &ptr);
  int64_t exu0Pressure(size_t index) const;
  const UarchConfig &cfg_;
  Registers &regs_;
  SHQ &shq_;
  EXQ &exq_;
  EXU &exu_;
  TraceRecorder &trace_;
  std::map<std::string, int> fuRr_{{"ALU", 0}, {"SFU", 0}};
  int rr_ = 0;
};
} // namespace vfsim
