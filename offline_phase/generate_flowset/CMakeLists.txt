cmake_minimum_required(VERSION 3.13)

project(FLOWSET_GENERATOR
    VERSION 1.0
    DESCRIPTION "5G flowset generator for real-time schedulability test benchmark."
    LANGUAGES CXX)
    
include_directories(${CMAKE_SOURCE_DIR}/include) # choosing header files folder


# choosing cpp file folders
file(GLOB SRC_SOURCES src/*.cpp)
file(GLOB LIB_SOURCES lib/*.cpp)
add_executable(generate_flowset ${SRC_SOURCES} ${LIB_SOURCES})

set_target_properties(generate_flowset PROPERTIES RUNTIME_OUTPUT_DIRECTORY ${CMAKE_SOURCE_DIR}) # defining the output file directory as the root TP folder

target_include_directories(generate_flowset PRIVATE ${CMAKE_SOURCE_DIR}/include)
target_compile_features(generate_flowset PRIVATE cxx_std_20) # selecting the compiler
