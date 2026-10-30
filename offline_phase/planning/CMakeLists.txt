cmake_minimum_required(VERSION 3.13)

project(MCS_PLANNER
    VERSION 1.0
    DESCRIPTION "Generates all existing solutions for a given 5G flowset."
    LANGUAGES CXX)
    
include_directories(${CMAKE_SOURCE_DIR}/include)
include_directories(${CMAKE_SOURCE_DIR}/../../common) # shared Section V implementation


# choosing cpp file folders
file(GLOB SRC_SOURCES src/*.cpp)
file(GLOB LIB_SOURCES lib/*.cpp)
add_executable(planner ${SRC_SOURCES} ${LIB_SOURCES})

set_target_properties(planner PROPERTIES RUNTIME_OUTPUT_DIRECTORY ${CMAKE_SOURCE_DIR}) # defining the output file directory as the root TP folder

target_include_directories(planner PRIVATE ${CMAKE_SOURCE_DIR}/include ${CMAKE_SOURCE_DIR}/../../common)
target_compile_features(planner PRIVATE cxx_std_20) # selecting the compiler
