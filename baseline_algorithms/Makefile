SHELL := /bin/bash

CXX      := g++
CXXFLAGS := -std=c++17 -O2 -Wall -Wextra -pedantic -I. -I../common

# Inside a conda environment (install_conda.sh) headers such as nlohmann/json
# live under $CONDA_PREFIX rather than in the system paths.
ifdef CONDA_PREFIX
CXXFLAGS += -I$(CONDA_PREFIX)/include
endif

SRCS     := $(wildcard */main.cpp)
PROJECTS := $(patsubst %/main.cpp,%,$(SRCS))

.PHONY: all clean $(PROJECTS)

all: $(PROJECTS)

$(PROJECTS):
	CUR_DIR=$$(pwd) && . ./../scripts/parameters.sh && cd "$$CUR_DIR" && $(CXX) $(CXXFLAGS) $(wildcard $@/*.cpp $@/*.cc) $(wildcard common/*.cpp common/*.cc) -o $@/$@

clean:
	rm -f $(foreach proj,$(PROJECTS),$(proj)/$(proj))