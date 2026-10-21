#pragma once
#include "native/core/execution/instruction.h"
#include "native/core/params/database.h"
#include <map>
#include <unordered_set>
#include <utility>
namespace vfsim {
class EXU {
public:
  EXU(const UarchConfig &cfg, const ParamDB &db);
  bool legalPort(const Uop &u, int port) const;
  bool canAccept(int port, bool queued) const;
  int64_t nextSelfIssueCycle(const Uop &u, int port) const;
  int64_t nextCompletionIssueCycle(const Uop &u, int port, int64_t cycle) const;
  int64_t nextIssueCycle(const Uop &u, int port, bool perFu = false,
                         int64_t notBefore = -1000000000) const;
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
  std::vector<std::map<std::pair<std::string, std::string>, int64_t>> selfIssueReady_;
  std::vector<std::unordered_set<int64_t>> completionCycles_;
  std::vector<int64_t> inflight_;
  std::map<int64_t, UopPtr> running_;
  int rr_ = 0;
};
} // namespace vfsim
