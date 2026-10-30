cmake_minimum_required(VERSION 3.13)

project(SIREN
    VERSION 1.0
    DESCRIPTION "SIREN: the time-indexed ILP scheduler, solved with Gurobi."
    LANGUAGES CXX
)

# C++17, matching the original g++ command
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# Default to a Release build.
# Without this CMAKE_BUILD_TYPE is empty for single-config generators, so the
# $<CONFIG:Release> genex below never matches and the build silently runs
# unoptimized.
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
    set_property(CACHE CMAKE_BUILD_TYPE PROPERTY STRINGS
        Debug Release RelWithDebInfo MinSizeRel)
endif()

# Find Gurobi (C API).
# Gurobi ships neither a CMake package nor a pkg-config file, so it is found
# through GUROBI_HOME: the linux64 directory of the unpacked distribution
# (e.g. ~/gurobi1301/linux64), given as -DGUROBI_HOME=... or in the
# environment. The library is the one of the header's version (13.0):
# libgurobi130.so. The build tree's RPATH points at it, so ./siren runs
# without LD_LIBRARY_PATH.
set(GUROBI_HOME "$ENV{GUROBI_HOME}" CACHE PATH "Gurobi's linux64 directory")
find_path(GUROBI_INCLUDE_DIR gurobi_c.h
    HINTS ${GUROBI_HOME}/include
    NO_DEFAULT_PATH
)
find_library(GUROBI_LIBRARY NAMES gurobi130
    HINTS ${GUROBI_HOME}/lib
    NO_DEFAULT_PATH
)
if(NOT GUROBI_INCLUDE_DIR OR NOT GUROBI_LIBRARY)
    message(FATAL_ERROR "Gurobi 13.0 not found under GUROBI_HOME='${GUROBI_HOME}' "
                        "(needs include/gurobi_c.h and lib/libgurobi130.so)")
endif()
message(STATUS "Found Gurobi: ${GUROBI_LIBRARY}")

# nlohmann/json is a real dependency of the scheduler headers; ask for it
# explicitly instead of relying on it happening to sit in a default include path.
find_package(nlohmann_json 3.2.0 REQUIRED)

# Source files
file(GLOB SRC_SOURCES CONFIGURE_DEPENDS
    ${PROJECT_SOURCE_DIR}/src/*.cpp
)

file(GLOB LIB_SOURCES CONFIGURE_DEPENDS
    ${PROJECT_SOURCE_DIR}/lib/*.cpp
)

# Executable
add_executable(siren
    ${SRC_SOURCES}
    ${LIB_SOURCES}
)

# Project headers
target_include_directories(siren
    PRIVATE
        ${PROJECT_SOURCE_DIR}/include
        ${GUROBI_INCLUDE_DIR}
)

# Gurobi + nlohmann/json
target_link_libraries(siren
    PRIVATE
        ${GUROBI_LIBRARY}
        nlohmann_json::nlohmann_json
)

# Optimization
target_compile_options(siren
    PRIVATE
        $<$<CONFIG:Release>:-O2>
)

# Put executable in the project root
set_target_properties(siren PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY ${PROJECT_SOURCE_DIR}
)