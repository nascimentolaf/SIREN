CXX      := g++
CXXFLAGS := -std=c++17 -O2 -Wall -Wextra -pedantic -I.

.PHONY: test clean

test: test_urllc_config
	./test_urllc_config

test_urllc_config: test_urllc_config.cpp urllc_config.hpp
	$(CXX) $(CXXFLAGS) $< -o $@

clean:
	rm -f test_urllc_config
