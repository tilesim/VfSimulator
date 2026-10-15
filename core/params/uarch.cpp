// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "core/params/uarch.h"
#include <stdexcept>
#include <type_traits>
#include <unordered_map>
namespace vfsim {
namespace {
using FieldBinding = std::variant<int64_t UarchConfig::*, bool UarchConfig::*,
                                  std::string UarchConfig::*,
                                  std::optional<int64_t> UarchConfig::*>;
const std::unordered_map<std::string, FieldBinding> &fieldBindings() {
  static const std::unordered_map<std::string, FieldBinding> bindings{
      {"name", &UarchConfig::name},
      {"issue_ports", &UarchConfig::issuePorts},
      {"load_ports", &UarchConfig::loadPorts},
      {"store_ports", &UarchConfig::storePorts},
      {"ub_slots", &UarchConfig::ubSlots},
      {"ub_bandwidth_bytes_per_cycle", &UarchConfig::ubBandwidthBytesPerCycle},
      {"IDU_window_width", &UarchConfig::iduWindowWidth},
      {"IDU_issue_width", &UarchConfig::iduIssueWidth},
      {"LDQ_width", &UarchConfig::ldqWidth},
      {"vreg_num", &UarchConfig::vregNum},
      {"physical_predicate_registers",
       &UarchConfig::physicalPredicateRegisters},
      {"shq_depth", &UarchConfig::shqDepth},
      {"exq_depth", &UarchConfig::exqDepth},
      {"exq_issue_inflight_cap_per_port",
       &UarchConfig::exqIssueInflightCapPerPort},
      {"canonical_dynamic_instruction_limit",
       &UarchConfig::canonicalDynamicInstructionLimit},
      {"idu_to_ooo_delay", &UarchConfig::iduToOooDelay},
      {"ooo_to_shq_delay", &UarchConfig::oooToShqDelay},
      {"ooo_to_lsq_delay", &UarchConfig::oooToLsqDelay},
      {"exq_recv_delay", &UarchConfig::exqRecvDelay},
      {"shq_to_exq_port_per_cycle", &UarchConfig::shqToExqPortPerCycle},
      {"shq_release_delay", &UarchConfig::shqReleaseDelay},
      {"idu_visible_preg_delay", &UarchConfig::iduVisiblePregDelay},
      {"idu_visible_shq_delay", &UarchConfig::iduVisibleShqDelay},
      {"idu_post_update_ready_latency",
       &UarchConfig::iduPostUpdateReadyLatency},
      {"consumer_release_start_offset",
       &UarchConfig::consumerReleaseStartOffset},
      {"vloop_to_dispatch_delay", &UarchConfig::vloopToDispatchDelay},
      {"idu_dispatch_start_advance", &UarchConfig::iduDispatchStartAdvance},
      {"initial_top_block_vloop_start_cycle",
       &UarchConfig::initialTopBlockVloopStartCycle},
      {"nested_vloop_initial_start_gap",
       &UarchConfig::nestedVloopInitialStartGap},
      {"loop1_min_feedback_gap", &UarchConfig::loop1MinFeedbackGap},
      {"innermost_iter_dispatch_stride",
       &UarchConfig::innermostIterDispatchStride},
      {"lsu_store_priority_preg_threshold",
       &UarchConfig::lsuStorePriorityPregThreshold},
      {"shq_exq_dispatch_policy", &UarchConfig::shqExqDispatchPolicy},
      {"exu0_reserve_lookahead", &UarchConfig::exu0ReserveLookahead},
      {"exu0_reserve_min_count", &UarchConfig::exu0ReserveMinCount},
      {"enable_isu_queue_model", &UarchConfig::enableIsuQueueModel},
      {"enable_shq_credit_model", &UarchConfig::enableShqCreditModel},
      {"enable_credit_visibility_delay",
       &UarchConfig::enableCreditVisibilityDelay},
      {"global_shq_preg_gate", &UarchConfig::globalShqPregGate},
      {"use_explicit_idu_credit_bank", &UarchConfig::useExplicitIduCreditBank},
      {"exq_capacity_counts_inflight", &UarchConfig::exqCapacityCountsInflight},
      {"enforce_same_cycle_src_hazard",
       &UarchConfig::enforceSameCycleSrcHazard},
      {"enable_cross_fu_ii", &UarchConfig::enableCrossFuIi},
      {"enable_exq_greedy_balance", &UarchConfig::enableExqGreedyBalance},
      {"assert_start_release_integrity",
       &UarchConfig::assertStartReleaseIntegrity},
      {"predict_exq_issue_with_cross_fu",
       &UarchConfig::predictExqIssueWithCrossFu},
      {"shq_exq_static_rr", &UarchConfig::shqExqStaticRr},
      {"theoretical_limit_mode", &UarchConfig::theoreticalLimitMode},
      {"theoretical_limit_vloop_only", &UarchConfig::theoreticalLimitVloopOnly},
      {"theoretical_limit_legacy_forwarding",
       &UarchConfig::theoreticalLimitLegacyForwarding},
      {"theoretical_limit_direct_issue",
       &UarchConfig::theoreticalLimitDirectIssue},
      {"theoretical_limit_vloop_to_dispatch_delay",
       &UarchConfig::theoreticalLimitVloopToDispatchDelay},
      {"theoretical_limit_idu_dispatch_start_advance",
       &UarchConfig::theoreticalLimitIduDispatchStartAdvance},
      {"theoretical_limit_idu_window_width",
       &UarchConfig::theoreticalLimitIduWindowWidth},
      {"theoretical_limit_idu_issue_width",
       &UarchConfig::theoreticalLimitIduIssueWidth},
      {"theoretical_limit_ldq_width", &UarchConfig::theoreticalLimitLdqWidth},
      {"theoretical_limit_shq_depth", &UarchConfig::theoreticalLimitShqDepth},
      {"theoretical_limit_exq_depth", &UarchConfig::theoreticalLimitExqDepth},
      {"theoretical_limit_idu_to_ooo_delay",
       &UarchConfig::theoreticalLimitIduToOooDelay},
      {"theoretical_limit_ooo_to_shq_delay",
       &UarchConfig::theoreticalLimitOooToShqDelay},
      {"theoretical_limit_ooo_to_lsq_delay",
       &UarchConfig::theoreticalLimitOooToLsqDelay},
      {"theoretical_limit_exq_recv_delay",
       &UarchConfig::theoreticalLimitExqRecvDelay},
      {"theoretical_limit_shq_release_delay",
       &UarchConfig::theoreticalLimitShqReleaseDelay},
      {"theoretical_limit_idu_visible_preg_delay",
       &UarchConfig::theoreticalLimitIduVisiblePregDelay},
      {"theoretical_limit_idu_visible_shq_delay",
       &UarchConfig::theoreticalLimitIduVisibleShqDelay},
      {"theoretical_limit_shq_to_exq_port_per_cycle",
       &UarchConfig::theoreticalLimitShqToExqPortPerCycle},
      {"theoretical_limit_exq_issue_inflight_cap_per_port",
       &UarchConfig::theoreticalLimitExqIssueInflightCapPerPort},
  };
  return bindings;
}
void assignField(UarchConfig &cfg, const std::string &name,
                 const CanonicalScalar &value) {
  const auto it = fieldBindings().find(name);
  if (it == fieldBindings().end())
    throw std::runtime_error("Unknown uarch field: " + name);
  const bool assigned = std::visit(
      [&](auto member) {
        using Member = std::decay_t<decltype(cfg.*member)>;
        using Scalar =
            std::conditional_t<std::is_same_v<Member, std::optional<int64_t>>,
                               int64_t, Member>;
        if (const auto *scalar = std::get_if<Scalar>(&value)) {
          cfg.*member = *scalar;
          return true;
        }
        return false;
      },
      it->second);
  if (!assigned)
    throw std::runtime_error("uarch." + name + " has invalid type");
}
int64_t timingInt(const json::Value::Object &obj, const char *name) {
  auto it = obj.find(name);
  if (it == obj.end() || !it->second.isInt() || it->second.asInt() < 0)
    throw std::runtime_error(std::string("invalid membar_timing integer: ") +
                             name);
  return it->second.asInt();
}
const json::Value::Object &objectField(const json::Value::Object &obj,
                                       const char *name) {
  auto it = obj.find(name);
  if (it == obj.end() || !it->second.isObject())
    throw std::runtime_error(std::string("membar_timing requires object: ") +
                             name);
  return it->second.asObject();
}
void check(const UarchConfig &cfg) {
  if (cfg.issuePorts < 1 || cfg.issuePorts > 2)
    throw std::runtime_error("The current model supports one or two EXU ports");
  if (cfg.physicalPredicateRegisters <= 0 ||
      cfg.physicalPredicateRegisters > 2147483647)
    throw std::runtime_error(
        "physical_predicate_registers must be a positive int32");
  if (cfg.iduPostUpdateReadyLatency <= 0)
    throw std::runtime_error("idu_post_update_ready_latency must be positive");
}
} // namespace
std::set<std::string> supportedUarchFields() {
  std::set<std::string> names;
  for (const auto &[name, member] : fieldBindings())
    names.insert(name);
  return names;
}
UarchConfig parseUarch(const json::Value::Object &values, UarchConfig cfg) {
  for (const auto &[name, value] : values) {
    if (name == "membar_timing") {
      if (!value.isObject())
        throw std::runtime_error("membar_timing must be an object");
      const auto &obj = value.asObject();
      MembarTimingConfig timing;
      timing.admissionDelay = timingInt(obj, "admission_delay");
      const auto &feedback = objectField(obj, "lsu_start_to_next_issue");
      for (const auto &kind : {"LOAD", "STORE"})
        timing.startFeedback[kind] = timingInt(feedback, kind);
      const auto &directions = objectField(obj, "directions");
      for (const auto &kind : {"VLD_VST", "VST_VLD"}) {
        const auto &rule = objectField(directions, kind);
        MembarDirectionTiming t{timingInt(rule, "release_latency"),
                                timingInt(rule, "retire_latency"),
                                timingInt(rule, "consumer_delay"),
                                timingInt(rule, "next_issue_delay")};
        if (t.retireLatency < t.releaseLatency || t.nextIssueDelay < 1)
          throw std::runtime_error("invalid membar_timing release/retirement");
        timing.directions[kind] = t;
      }
      cfg.membarTiming = timing;
      continue;
    }
    if (value.isInt())
      assignField(cfg, name, value.asInt());
    else if (value.isBool())
      assignField(cfg, name, value.asBool());
    else if (value.isString())
      assignField(cfg, name, value.asString());
    else
      throw std::runtime_error("Invalid uarch value: " + name);
  }
  check(cfg);
  return cfg;
}
UarchConfig resolveUarch(UarchConfig cfg,
                         const std::map<std::string, CanonicalScalar> &values,
                         bool vloopOnly, bool directIssue) {
  for (const auto &[name, value] : values)
    assignField(cfg, name, value);
  check(cfg);
  if (vloopOnly || directIssue)
    cfg.theoreticalLimitMode = cfg.theoreticalLimitVloopOnly = true;
  if (directIssue)
    cfg.theoreticalLimitLegacyForwarding = cfg.theoreticalLimitDirectIssue =
        true;
  if (!cfg.theoreticalLimitMode)
    return cfg;
  constexpr int64_t huge = 1000000000000000000LL;
  cfg.vloopToDispatchDelay =
      cfg.theoreticalLimitVloopToDispatchDelay.value_or(4);
  cfg.iduDispatchStartAdvance =
      cfg.theoreticalLimitIduDispatchStartAdvance.value_or(0);
  cfg.iduWindowWidth = cfg.theoreticalLimitIduWindowWidth.value_or(huge);
  cfg.iduIssueWidth = cfg.theoreticalLimitIduIssueWidth.value_or(huge);
  cfg.ldqWidth = cfg.theoreticalLimitLdqWidth.value_or(huge);
  cfg.shqDepth = cfg.theoreticalLimitShqDepth.value_or(huge);
  cfg.exqDepth = cfg.theoreticalLimitExqDepth.value_or(huge);
  cfg.iduToOooDelay = cfg.theoreticalLimitIduToOooDelay.value_or(0);
  cfg.oooToShqDelay = cfg.theoreticalLimitOooToShqDelay.value_or(0);
  cfg.oooToLsqDelay = cfg.theoreticalLimitOooToLsqDelay.value_or(0);
  cfg.exqRecvDelay = cfg.theoreticalLimitExqRecvDelay.value_or(0);
  cfg.shqReleaseDelay = cfg.theoreticalLimitShqReleaseDelay.value_or(0);
  cfg.iduVisiblePregDelay = cfg.theoreticalLimitIduVisiblePregDelay.value_or(0);
  cfg.iduVisibleShqDelay = cfg.theoreticalLimitIduVisibleShqDelay.value_or(0);
  cfg.shqToExqPortPerCycle =
      cfg.theoreticalLimitShqToExqPortPerCycle.value_or(huge);
  cfg.exqIssueInflightCapPerPort =
      cfg.theoreticalLimitExqIssueInflightCapPerPort.value_or(0);
  if (cfg.theoreticalLimitDirectIssue) {
    cfg.enableIsuQueueModel = cfg.enableShqCreditModel =
        cfg.enableCreditVisibilityDelay = false;
    cfg.exqRecvDelay = cfg.exqIssueInflightCapPerPort = 0;
    cfg.shqToExqPortPerCycle = huge;
  }
  return cfg;
}
} // namespace vfsim
