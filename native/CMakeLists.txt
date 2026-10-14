cmake_minimum_required(VERSION 3.20)
project(VfSimulatorNative LANGUAGES CXX)
include(CTest)
option(VFSIM_BUILD_TESTS "Build native simulator tests" ${PROJECT_IS_TOP_LEVEL})
add_library(vfsim_native_core STATIC
  ../api/native/common/diagnostics.cpp
  ../api/native/common/instruction_catalog.cpp
  ../api/native/common/schema.cpp
  ../api/native/common/uarch_validation.cpp
  ../api/native/common/validator.cpp
  ../api/native/input_api.cpp
  ../api/native/json/serialization.cpp
  ../api/native/json/value.cpp
  execution/engine.cpp
  execution/expansion.cpp
  execution/loop_control.cpp
  execution/membar.cpp
  execution/prepare.cpp
  params/database.cpp
  params/fallback.cpp
  params/loader.cpp
  params/uarch.cpp
  simulator.cpp
  trace/recorder.cpp
  trace/writer.cpp
  uarch/exq.cpp
  uarch/exu.cpp
  uarch/idu.cpp
  uarch/lsu.cpp
  uarch/ooo.cpp
  uarch/registers.cpp
)
add_library(vfsim::native_core ALIAS vfsim_native_core)
target_include_directories(vfsim_native_core PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/..)
target_compile_features(vfsim_native_core PUBLIC cxx_std_17)
target_compile_definitions(vfsim_native_core PUBLIC VFSIM_SOURCE_ROOT="${CMAKE_CURRENT_SOURCE_DIR}/..")
add_executable(vfsim_native_json_runner cli/json_runner.cpp)
target_link_libraries(vfsim_native_json_runner PRIVATE vfsim::native_core)
if(VFSIM_BUILD_TESTS AND BUILD_TESTING)
  add_subdirectory(tests)
endif()
