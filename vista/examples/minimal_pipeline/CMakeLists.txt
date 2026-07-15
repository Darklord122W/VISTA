# CMakeLists.txt — minimal_pipeline (VISTA integration template).
#
# Standalone build (adds the vista sources from ../..):
#   cmake -B build && cmake --build build
#
# Against an INSTALLED vista package (what a real integration does):
#   cmake -B build -DUSE_INSTALLED_VISTA=ON -DCMAKE_PREFIX_PATH=/your/prefix
#   cmake --build build
#
# NOTE: CMAKE_BUILD_TYPE is deliberately left empty by default. A Release build
# defines NDEBUG, which deletes the assert(st.ledger_closes()) that is the point
# of this example. (main.cpp also fails the exit code explicitly, so the check
# survives NDEBUG — but the assert should be the thing that fires.)

cmake_minimum_required(VERSION 3.16)
project(minimal_pipeline LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

option(USE_INSTALLED_VISTA
       "Link an installed vista package instead of building ../.. in-tree" OFF)

if(USE_INSTALLED_VISTA)
  find_package(vista REQUIRED)
else()
  # In-tree: the parent CMakeLists defines the same vista::scheduler target.
  add_subdirectory(${CMAKE_CURRENT_SOURCE_DIR}/../.. vista_build)
endif()

find_package(PkgConfig REQUIRED)
pkg_check_modules(GST REQUIRED IMPORTED_TARGET gstreamer-1.0)

set(DS_ROOT "/opt/nvidia/deepstream/deepstream" CACHE PATH "DeepStream SDK root")

add_executable(minimal_pipeline main.cpp)
target_link_libraries(minimal_pipeline PRIVATE vista::scheduler PkgConfig::GST)
target_compile_options(minimal_pipeline PRIVATE -Wall -Wextra)
