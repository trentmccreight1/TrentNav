cmake_minimum_required(VERSION 3.24)
project(TrentNavTools LANGUAGES C CXX)

if(NOT WIN32)
  message(FATAL_ERROR "The initial client probe supports Windows only")
endif()

set(TRINITY_SOURCE "" CACHE PATH "Pinned extraction dependency checkout")
if(NOT EXISTS "${TRINITY_SOURCE}/dep/CascLib/src/CascLib.h")
  message(FATAL_ERROR "Set TRINITY_SOURCE to the pinned local source checkout")
endif()

add_library(trinity-dependency-interface INTERFACE)
target_compile_definitions(trinity-dependency-interface INTERFACE
  _CRT_SECURE_NO_WARNINGS NOMINMAX WIN32_LEAN_AND_MEAN _WIN32_WINNT=0x0A00)
add_subdirectory("${TRINITY_SOURCE}/dep/zlib" "dependencies/zlib")
add_subdirectory("${TRINITY_SOURCE}/dep/CascLib" "dependencies/casc")
add_subdirectory("${TRINITY_SOURCE}/dep/recastnavigation" "dependencies/recast")

add_executable(trentnav_client_probe client_probe.cpp)
target_compile_features(trentnav_client_probe PRIVATE cxx_std_20)
target_compile_definitions(trentnav_client_probe PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN)
target_link_libraries(trentnav_client_probe PRIVATE casc ws2_32)

add_executable(trentnav_mesh_smoke mesh_smoke.cpp)
target_compile_features(trentnav_mesh_smoke PRIVATE cxx_std_20)
target_compile_definitions(trentnav_mesh_smoke PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN)
target_link_libraries(trentnav_mesh_smoke PRIVATE Recast Detour)

add_executable(trentnav_mesh_query mesh_query.cpp)
target_compile_features(trentnav_mesh_query PRIVATE cxx_std_20)
target_compile_definitions(trentnav_mesh_query PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN)
target_link_libraries(trentnav_mesh_query PRIVATE Detour)

if(MSVC)
  target_compile_options(trentnav_client_probe PRIVATE /W4 /WX /EHsc)
  target_compile_options(trentnav_mesh_smoke PRIVATE /W4 /WX /EHsc)
  target_compile_options(trentnav_mesh_query PRIVATE /W4 /WX /EHsc)
endif()
