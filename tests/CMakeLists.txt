foreach(test IN ITEMS input_api address_state predicate_register membar_timing components parameters programmatic cce execution_fast_paths compact_execution scoring_stream)
  add_executable(vfsim_${test}_test ${test}_test.cpp)
  target_link_libraries(vfsim_${test}_test PRIVATE vfsim::native_core)
  add_test(NAME vfsim_${test}_test COMMAND vfsim_${test}_test)
endforeach()

# The streamed scoring route is opt-in through the CLI environment switch, so
# the unit test above cannot see the parsing. These run the real runner and
# assert the documented contract: only "1" enables it, --out_dir keeps the eager
# trace/export path, and --help documents the contract.
set(vfsim_cli $<TARGET_FILE:vfsim_native_json_runner>)
set(vfsim_loop_fixture ${CMAKE_CURRENT_SOURCE_DIR}/../VFtest/canonical/fixtures/v1_valid_loop.json)
set(vfsim_carried_fixture ${CMAKE_CURRENT_SOURCE_DIR}/../VFtest/canonical/fixtures/v1_valid_loop_carried.json)

function(vfsim_add_cli_stream_case name env_value expect_regex)
  set(workdir ${CMAKE_CURRENT_BINARY_DIR}/cli_${name})
  file(MAKE_DIRECTORY ${workdir})
  add_test(NAME vfsim_cli_stream_${name}
           COMMAND ${vfsim_cli} --trace ${ARGN})
  set_tests_properties(vfsim_cli_stream_${name} PROPERTIES
    WORKING_DIRECTORY ${workdir}
    ENVIRONMENT "VFSIM_SCORING_STREAM=${env_value}"
    PASS_REGULAR_EXPRESSION "${expect_regex}")
endfunction()

# "1" selects the streamed preparation and nothing else does.
vfsim_add_cli_stream_case(on 1 "scoringMode=scoring" ${vfsim_loop_fixture})
vfsim_add_cli_stream_case(on_carried 1 "scoringMode=scoring" ${vfsim_carried_fixture})
foreach(off_value 0 false no off TRUE)
  vfsim_add_cli_stream_case(off_${off_value} "${off_value}" "scoringMode=eager" ${vfsim_loop_fixture})
endforeach()
# An explicit results directory is a trace/export request and wins over the switch.
vfsim_add_cli_stream_case(refused_explicit_out_dir 1 "scoringStream=refused:explicit-out-dir"
                          ${vfsim_loop_fixture} --out_dir ${CMAKE_CURRENT_BINARY_DIR}/cli_refused_out)

add_test(NAME vfsim_cli_help_documents_switch COMMAND ${vfsim_cli} --help)
set_tests_properties(vfsim_cli_help_documents_switch PROPERTIES
  PASS_REGULAR_EXPRESSION "VFSIM_SCORING_STREAM=1")
