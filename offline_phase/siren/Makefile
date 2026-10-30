EXE=siren

.PHONY: run clean

all:
	mkdir -p build
	cd build && cmake .. && make -j$(nproc) && cd ..

run:
	./$(EXE)

# Build artifacts only. This used to also wipe ../../output/flowsets/*, which
# install.sh triggers through `make clean` on every rebuild -- and those
# flowsets are the committed inputs the methods are compared on, not something
# a rebuild may regenerate. The experiment scripts clear and regenerate them
# when REUSE_FLOWSETS=0 (scripts/parameters.sh).
clean:
	rm -rf $(EXE) build/*
