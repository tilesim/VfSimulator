cmake_minimum_required(VERSION 3.20)
project(VfSimulator LANGUAGES CXX)

include(CTest)
option(VFSIM_BUILD_TESTS "Build simulator tests" ${PROJECT_IS_TOP_LEVEL})
add_library(vfsim_native_core STATIC
  api/common/diagnostics.cpp
  api/common/instruction_catalog.cpp
  api/common/instruction_validation.cpp
  api/common/schema.cpp
  api/common/uarch_validation.cpp
  api/common/validator.cpp
  api/cce/source.cpp
  api/cce/affine.cpp
  api/cce/normalization.cpp
  api/cce/parser.cpp
  api/cce/value_versioning.cpp
  api/cce/adapter.cpp
  api/programmatic/builder.cpp
  api/input_api.cpp
  api/json/serialization.cpp
  api/json/value.cpp
  core/execution/engine.cpp
  core/execution/expansion.cpp
  core/execution/loop_control.cpp
  core/execution/membar.cpp
  core/execution/prepare.cpp
  core/params/database.cpp
  core/params/fallback.cpp
  core/params/loader.cpp
  core/params/uarch.cpp
  core/simulator.cpp
  core/trace/perfetto.cpp
  core/trace/recorder.cpp
  core/trace/writer.cpp
  core/uarch/exq.cpp
  core/uarch/exu.cpp
  core/uarch/idu.cpp
  core/uarch/lsu.cpp
  core/uarch/ooo.cpp
  core/uarch/registers.cpp
)
add_library(vfsim::native_core ALIAS vfsim_native_core)
target_include_directories(vfsim_native_core PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
target_compile_features(vfsim_native_core PUBLIC cxx_std_17)
target_compile_definitions(vfsim_native_core PUBLIC VFSIM_SOURCE_ROOT="${CMAKE_CURRENT_SOURCE_DIR}")
add_executable(vfsim_native_json_runner cli/json_runner.cpp)
target_link_libraries(vfsim_native_json_runner PRIVATE vfsim::native_core)
if(VFSIM_BUILD_TESTS AND BUILD_TESTING)
  add_subdirectory(tests)
endif()
