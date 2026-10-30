EXE=planner

.PHONY: run clean

all:
	mkdir -p build
	cd build && cmake .. && make -j$(nproc) && cd ..

run:
	./$(EXE)

clean:
	rm -rf $(EXE) build/*
