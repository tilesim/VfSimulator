#pragma once
#include "native/core/execution/instruction.h"
#include "native/core/params/database.h"
#include <array>
namespace vfsim {
class EXU {
public:
  EXU(const UarchConfig &cfg, const ParamDB &db);
  bool legalPort(const Uop &u, int port) const;
  bool canAccept(int port, bool queued) const;
  int64_t nextIssueCycle(const Uop &u, int port, bool perFu = false) const;
  bool canIssue(const Uop &u, int port, int64_t cycle, bool queued) const;
  std::optional<int> pickPort(const Uop &u, int64_t cycle,
                              const std::vector<bool> &used);
  void start(const UopPtr &u, int port, int64_t cycle, bool queued);
  std::vector<UopPtr> finish(int64_t cycle);
  int64_t inflight(int port) const { return inflight_.at(port); }

private:
  struct LastIssue {
    int64_t cycle = -1000000000;
    std::string op, form;
  };
  const UarchConfig &cfg_;
  const ParamDB &db_;
  std::vector<LastIssue> lastPort_;
  std::vector<std::map<std::string, LastIssue>> lastFu_;
  std::vector<int64_t> inflight_;
  std::map<int64_t, UopPtr> running_;
  int rr_ = 0;
};
} // namespace vfsim
