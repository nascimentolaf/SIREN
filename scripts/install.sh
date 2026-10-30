#!/bin/bash

set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GENERATOR_DIR=$DIR/offline_phase/generate_flowset
PLANNER_DIR=$DIR/offline_phase/planning
EVALUATION_DIR=$DIR/offline_phase
BASELINE_ALGORITHMS_DIR=$DIR/baseline_algorithms

build_project() {
    local dir="$1"

    echo "========================================"
    echo "Building: $dir"
    echo "========================================"

    cd "$dir"
    make clean
    make -j$(nproc)
    echo ""
}

# nlohmann/json for the ILP: offline_phase/siren/CMakeLists.txt asks
# for the nlohmann_json CMake package, shipped by nlohmann-json3-dev on
# Debian/Ubuntu (every other component bundles its own json.hpp). The check
# runs that same find_package in a throwaway project.
json_probe=$(mktemp -d)
printf 'cmake_minimum_required(VERSION 3.13)\nproject(probe NONE)\nfind_package(nlohmann_json 3.2.0 REQUIRED)\n' >"$json_probe/CMakeLists.txt"
if cmake -S "$json_probe" -B "$json_probe/build" >/dev/null 2>&1; then
    echo "nlohmann/json is already installed."
else
    echo "nlohmann/json not found. Installing nlohmann-json3-dev..."
    sudo apt-get update && sudo apt-get install -y nlohmann-json3-dev
fi
rm -rf "$json_probe"

build_project "$GENERATOR_DIR"
build_project "$PLANNER_DIR"
build_project "$EVALUATION_DIR"/siren

# The baseline schedulers (rr, pf, qospf, must) share one Makefile
# that builds every <name>/main.cpp into <name>/<name>. run_paper_experiment.sh
# runs them alongside the ILP, so they have to be built here too.
build_project "$BASELINE_ALGORITHMS_DIR"
