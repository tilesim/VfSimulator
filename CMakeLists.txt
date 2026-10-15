cmake_minimum_required(VERSION 3.20)
project(VfSimulator LANGUAGES CXX)

include(CTest)
option(VFSIM_BUILD_TESTS "Build simulator tests" ${PROJECT_IS_TOP_LEVEL})
add_subdirectory(native)
