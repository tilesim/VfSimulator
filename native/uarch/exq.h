#pragma once
#include "native/uarch/exu.h"
#include "native/uarch/registers.h"
namespace vfsim {
class EXQ {
public:
  EXQ(const UarchConfig &cfg, const ParamDB &db, Registers &regs, EXU &exu);
  int64_t occupancy(int port) const;
  int64_t admissionOccupancy(int port) const;
  bool canAccept(int port) const {
    return admissionOccupancy(port) < cfg_.exqDepth;
  }
  int64_t predictIssueCycle(int port, const Uop &u, int64_t recv) const;
  void enqueue(const UopPtr &u, int port, int64_t recv, int64_t predicted);
  UopPtr popReady(int port, int64_t cycle);

private:
  const UarchConfig &cfg_;
  const ParamDB &db_;
  Registers &regs_;
  EXU &exu_;
  std::vector<std::map<std::string, std::deque<UopPtr>>> queues_;
};
} // namespace vfsim
