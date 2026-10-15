#pragma once
#include "api/common/schema.h"
#include <filesystem>
#include <map>
#include <string>
#include <vector>

namespace vfsim::cce {
struct VFScope {
  std::string kernelName, source, declarationSource, sourcePath;
  int64_t startLine = 1, endLine = 1;
  std::vector<std::string> params;
  std::map<std::string, std::string> paramStorage, paramDtypes;
};
std::string readSource(const std::filesystem::path &);
std::string stripComments(const std::string &);
std::string trim(const std::string &);
std::string lower(std::string);
std::string normalizeDtype(const std::string &);
std::string scalarDtype(const std::string &);
std::string compactDtype(const std::string &);
std::string normalizeForm(const std::string &);
std::string normalizeMembar(const std::string &);
std::vector<std::string> splitArguments(const std::string &);
size_t matchingDelimiter(const std::string &, size_t, char, char);
std::string stripUbWrappers(std::string);
std::string baseIdentifier(const std::string &);
int64_t lineNumber(const std::string &, size_t);
std::vector<VFScope> extractScopes(const std::string &,
                                   const std::filesystem::path &);
const VFScope &selectScope(const std::vector<VFScope> &, const std::string &);
std::vector<std::string> listKernels(const std::filesystem::path &);
} // namespace vfsim::cce
