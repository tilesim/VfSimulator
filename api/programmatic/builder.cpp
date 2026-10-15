// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0
#include "api/programmatic/builder.h"
#include <functional>
namespace vfsim {
VfInfoBuilder::VfInfoBuilder(std::unordered_map<std::string, int64_t> params,
                             std::map<std::string, CanonicalScalar> uarch,
                             std::map<std::string, CanonicalScalar> source) {
  vf_.params = std::move(params);
  vf_.uarch = std::move(uarch);
  vf_.source = std::move(source);
}
void VfInfoBuilder::reserve(const std::string &id) {
  if (id.empty() || !ids_.insert(id).second)
    throw std::invalid_argument("Duplicate or empty node ID: " + id);
}
CanonicalStorageObject
VfInfoBuilder::registerStorageObject(CanonicalStorageObject value) {
  if (!vf_.storageObjects.emplace(value.objectId, value).second)
    throw std::invalid_argument("Duplicate storage object ID: " +
                                value.objectId);
  return value;
}
CanonicalValue VfInfoBuilder::registerValue(CanonicalValue value) {
  if (!vf_.values.emplace(value.definitionId, value).second)
    throw std::invalid_argument("Duplicate value definition ID: " +
                                value.definitionId);
  return value;
}
CanonicalInstruction VfInfoBuilder::addInstruction(CanonicalInstruction value) {
  reserve(value.instructionId);
  bodies_.back().push_back(CanonicalNode::makeInstruction(value));
  return value;
}
CanonicalMembar VfInfoBuilder::addMembar(CanonicalMembar value) {
  reserve(value.instructionId);
  bodies_.back().push_back(CanonicalNode::makeMembar(value));
  return value;
}
CanonicalLoop VfInfoBuilder::addLoop(CanonicalLoop value) {
  auto ids = ids_;
  std::function<void(const CanonicalNode &)> collect = [&](const auto &node) {
    std::visit(
        [&](const auto &item) {
          using T = std::decay_t<decltype(item)>;
          std::string id;
          if constexpr (std::is_same_v<T,
                                       std::shared_ptr<const CanonicalLoop>>) {
            if (!item)
              throw std::invalid_argument("Null Canonical loop");
            id = item->loopId;
          } else
            id = item.instructionId;
          if (id.empty() || !ids.insert(id).second)
            throw std::invalid_argument("Duplicate or empty node ID: " + id);
          if constexpr (std::is_same_v<T, std::shared_ptr<const CanonicalLoop>>)
            for (const auto &child : item->body)
              collect(child);
        },
        node.payload);
  };
  collect(CanonicalNode::makeLoop(value));
  ids_ = std::move(ids);
  bodies_.back().push_back(CanonicalNode::makeLoop(value));
  return value;
}
CanonicalVfInfo VfInfoBuilder::build() const {
  if (bodies_.size() != 1)
    throw std::logic_error(
        "Cannot build CanonicalVfInfo inside an open loop scope");
  auto result = vf_;
  result.context = bodies_.front();
  result.schemaVersion = emissionSchemaVersion(result.values, result.context);
  requireValidCanonicalVfInfo(result);
  return result;
}
VfInfoBuilder::LoopScope::LoopScope(VfInfoBuilder &builder,
                                    CanonicalLoop definition)
    : builder_(&builder), definition_(std::move(definition)),
      before_(builder.vf_), idsBefore_(builder.ids_),
      depth_(builder.bodies_.size()),
      exceptionCount_(std::uncaught_exceptions()) {
  if (!definition_.body.empty())
    throw std::invalid_argument(
        "Loop scope body must be constructed through the builder");
  builder.reserve(definition_.loopId);
  builder.bodies_.emplace_back();
}
VfInfoBuilder::LoopScope::LoopScope(LoopScope &&other) noexcept
    : builder_(other.builder_), definition_(std::move(other.definition_)),
      before_(std::move(other.before_)),
      idsBefore_(std::move(other.idsBefore_)), depth_(other.depth_),
      exceptionCount_(other.exceptionCount_) {
  other.builder_ = nullptr;
}
VfInfoBuilder::LoopScope::~LoopScope() noexcept(false) {
  if (builder_)
    finish(std::uncaught_exceptions() == exceptionCount_);
}
void VfInfoBuilder::LoopScope::close() {
  if (builder_)
    finish(true);
}
void VfInfoBuilder::LoopScope::finish(bool commit) {
  if (builder_->bodies_.size() != depth_ + 1)
    throw std::logic_error("Builder loop scopes must close in stack order");
  auto body = std::move(builder_->bodies_.back());
  builder_->bodies_.pop_back();
  if (commit) {
    definition_.body = std::move(body);
    builder_->bodies_.back().push_back(
        CanonicalNode::makeLoop(std::move(definition_)));
  } else {
    builder_->vf_ = std::move(before_);
    builder_->ids_ = std::move(idsBefore_);
  }
  builder_ = nullptr;
}
} // namespace vfsim
