# CMakeLists.txt — libvista (VISTA scheduler), a static library you link into
# your own DeepStream application.
#
#   cmake -B build -DCMAKE_BUILD_TYPE=Release
#   cmake --build build -j
#   cmake --install build --prefix /where/you/want
#
# Consumers then do:
#   find_package(vista REQUIRED)
#   target_link_libraries(myapp PRIVATE vista::scheduler)
# which brings the include dirs, GStreamer, and the DeepStream meta libraries
# along transitively. See vista/README.md for the integration contract — this
# file only gets the symbols to link; it cannot make your pipeline correct.

cmake_minimum_required(VERSION 3.16)
project(vista VERSION 1.0.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# DeepStream is not pkg-config-discoverable, so its root is a cache variable.
# The default is the standard JetPack / DeepStream install location; override
# with -DDS_ROOT=/path if yours differs.
set(DS_ROOT "/opt/nvidia/deepstream/deepstream" CACHE PATH
    "DeepStream SDK root (contains sources/includes and lib)")

if(NOT EXISTS "${DS_ROOT}/sources/includes/gstnvdsmeta.h")
  message(FATAL_ERROR
      "DS_ROOT='${DS_ROOT}' does not look like a DeepStream install: "
      "sources/includes/gstnvdsmeta.h is missing. Pass -DDS_ROOT=<path>.")
endif()

find_package(PkgConfig REQUIRED)
pkg_check_modules(GST REQUIRED IMPORTED_TARGET gstreamer-1.0)

set(THREADS_PREFER_PTHREAD_FLAG ON)
find_package(Threads REQUIRED)

add_library(vista_scheduler STATIC src/vista_scheduler.cpp)
add_library(vista::scheduler ALIAS vista_scheduler)

# OUTPUT_NAME: so both build paths produce the same artifact name, libvista.a.
# The plain Makefile hardcodes that name, and an integrator who tries one path
# and then the other should not end up with two differently-named archives.
#
# EXPORT_NAME: without it the INSTALLED package exports "vista::vista_scheduler"
# (NAMESPACE + target name), while the ALIAS above gives in-tree consumers
# "vista::scheduler" — so the name this README documents would work via
# add_subdirectory and fail via find_package. Verified: a consumer project
# linking vista::scheduler against the installed package fails to configure
# without this line.
set_target_properties(vista_scheduler PROPERTIES
    OUTPUT_NAME vista
    EXPORT_NAME scheduler)

# INTERFACE include dirs: BUILD_INTERFACE for in-tree consumers (add_subdirectory),
# INSTALL_INTERFACE for consumers of the installed package. Both resolve
# #include "vista/vista_scheduler.hpp".
target_include_directories(vista_scheduler
  PUBLIC
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
    $<INSTALL_INTERFACE:include>
  PRIVATE
    ${DS_ROOT}/sources/includes)

# The DeepStream include dir is PRIVATE, and can be: vista_scheduler.hpp
# includes only <gst/gst.h>, while gstnvdsmeta.h is confined to the .cpp. So
# linking VISTA does not put the whole DeepStream include tree on a consumer's
# compile line. (Your own app will of course need it for its own DS calls.)
#
# The meta libraries are named by absolute path rather than -lnvdsgst_meta
# -lnvds_meta: it is the same two libraries, but this way a wrong DS_ROOT fails
# at configure time with the path in the message, instead of at link time with
# "cannot find -lnvds_meta". They are PUBLIC because this is a STATIC archive —
# it records no dependencies of its own, so the final executable is what must
# actually link them.
target_link_libraries(vista_scheduler
  PUBLIC
    PkgConfig::GST
    Threads::Threads
    ${DS_ROOT}/lib/libnvdsgst_meta.so
    ${DS_ROOT}/lib/libnvds_meta.so)

# -Wl,-rpath so the DeepStream libs resolve at run time without the consumer
# having to set LD_LIBRARY_PATH. INTERFACE, because it is the final executable's
# link line that needs it, not this static archive's.
target_link_options(vista_scheduler INTERFACE "-Wl,-rpath,${DS_ROOT}/lib")

target_compile_options(vista_scheduler PRIVATE -Wall -Wextra)

# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------
include(GNUInstallDirs)
include(CMakePackageConfigHelpers)

install(TARGETS vista_scheduler
        EXPORT vistaTargets
        ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR})

install(DIRECTORY include/vista
        DESTINATION ${CMAKE_INSTALL_INCLUDEDIR})

# The mux INI travels WITH the library, on purpose. It is not documentation: a
# VISTA deployment whose nvstreammux is configured differently produces numbers
# that are not comparable to the paper's, and the failure is silent (see the
# file's own header, and the runtime atomicity gate). Installing it here means
# an integrator who has the library always has the matching INI.
install(FILES config/mux_vista.txt
        DESTINATION ${CMAKE_INSTALL_DATADIR}/vista)

install(EXPORT vistaTargets
        FILE vistaTargets.cmake
        NAMESPACE vista::
        DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/vista)

# Generated so a consumer's find_package(vista) works. DS_ROOT is baked in
# because the installed archive's rpath already points at that DeepStream.
file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/vistaConfig.cmake.in"
"@PACKAGE_INIT@
include(CMakeFindDependencyMacro)
find_dependency(PkgConfig)
pkg_check_modules(GST REQUIRED IMPORTED_TARGET gstreamer-1.0)
find_dependency(Threads)
set(VISTA_DS_ROOT \"@DS_ROOT@\")
# Where the installed mux INI landed. Point your nvstreammux
# config-file-path at this, or copy it next to your app.
set(VISTA_MUX_INI \"@PACKAGE_CMAKE_INSTALL_DATADIR@/vista/mux_vista.txt\")
include(\"\${CMAKE_CURRENT_LIST_DIR}/vistaTargets.cmake\")
check_required_components(vista)
")

configure_package_config_file(
    "${CMAKE_CURRENT_BINARY_DIR}/vistaConfig.cmake.in"
    "${CMAKE_CURRENT_BINARY_DIR}/vistaConfig.cmake"
    INSTALL_DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/vista
    PATH_VARS CMAKE_INSTALL_DATADIR)

write_basic_package_version_file(
    "${CMAKE_CURRENT_BINARY_DIR}/vistaConfigVersion.cmake"
    VERSION ${PROJECT_VERSION}
    COMPATIBILITY SameMajorVersion)

install(FILES
        "${CMAKE_CURRENT_BINARY_DIR}/vistaConfig.cmake"
        "${CMAKE_CURRENT_BINARY_DIR}/vistaConfigVersion.cmake"
        DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/vista)
