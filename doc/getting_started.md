# Getting started

From a fresh checkout to a build, a checked catalogue and a first run. Paths
are relative to the repository root. To reproduce the paper's results,
continue with the run guide, `RUN_EXPERIMENTS.md` at the repository root.

## Prerequisites

Linux with Bash.

| Requirement | Used by |
| :--- | :--- |
| [Gurobi](https://www.gurobi.com) 13.0 (the paper's runs used 13.0.1), its C API from Gurobi's own distribution, unpacked; `GUROBI_HOME` set to its `linux64` directory | `siren` (build and run) |
| a Gurobi license, found through `GRB_LICENSE_FILE` or Gurobi's default places such as `~/gurobi.lic`. Each `siren` process takes one license token for its whole run | running `siren` |
| CMake ≥ 3.13, `make`, `g++` with C++20 support (`siren` and the baselines are built as C++17; the flowset generator and the planner ask CMake for C++20) | every C++ component |
| `nlohmann/json` installed system-wide (`nlohmann-json3-dev` on Debian/Ubuntu); `scripts/install.sh` installs it with `sudo apt-get` if CMake cannot find it | `siren`'s CMake build (`find_package(nlohmann_json)`); every other component bundles its own `json.hpp` |
| `bc`, `lscpu`, `taskset`, `flock`, `hostname`, `mktemp` (util-linux and coreutils) | `scripts/run_paper_experiment.sh` and `scripts/parameters.sh` |
| Python 3 with `numpy`, `pandas` and `matplotlib` | `analysis/plot_paper.py`, `analysis/plot_evaluation.py` |
| Python 3 (standard library only) | `scripts/check_catalogue.py`, `analysis/collect_verdicts.py`, `analysis/rq4_hyperperiod.py`, `offline_phase/siren/validate_schedule.py` |

Neither the repository nor its scripts install Gurobi or a license. The
`gurobipy` wheel from pip is not enough: `siren` needs the C headers and
library of the full distribution.

**For the link-level BLER simulation** (only to regenerate the MCS table, see
[BLER and MCS tables](bler.md)): Python ≥ 3.10 with the packages of
`bler/requirements.txt` (Sionna 1.x, TensorFlow, SciPy).

**For these docs**: the `markdown` Python package.

## Build

```bash
export GUROBI_HOME=/path/to/gurobi1301/linux64
(cd scripts && ./install.sh)      # or ./install_conda.sh inside an activated conda env
make -C common test               # expects: All Section V checks passed.
```

`install.sh` installs nlohmann/json if missing, then runs `make clean && make`
in, in order: the flowset generator, the planner, `siren`, and
`baseline_algorithms/` (the four baselines). It does **not** touch `output/`,
and does not build the unit test of the shared Section V code (the `make -C
common test` line above). Building needs no Gurobi license. CMake prints
`Found Gurobi: .../libgurobi130.so` when it finds Gurobi, and stops with
`Gurobi 13.0 not found under GUROBI_HOME=...` when it does not.

The `build` stage of `scripts/run_paper_experiment.sh` builds the planner, the
baselines and `siren` (not the flowset generator, which the experiments do not
run).

Rebuild after every change to the C++ code: the scripts run whatever
binaries are on disk.

## Check the inputs

The repository ships 1000 flowsets in `output/flowsets/`, drawn from the
30-application catalogue `input/app_database.json`. Check that the catalogue
satisfies the evaluation's invariants:

```bash
python3 scripts/check_catalogue.py
```

It prints the catalogue's deadlines, periods, Pareto-set sizes and candidate
counts, ends with `all invariants hold`, and exits non-zero if an application
is infeasible alone, if `D_i > T_i`, if a time is not a whole millisecond, if
the hyperperiod exceeds 420 ms, or if `C(30, 4)` is too small for the flowsets
requested. See [Inputs](input.md).

## A first run

`run_paper_experiment.sh` runs the paper's whole experiment on all 1000
flowsets, which takes long. Its options allow a small first run, on a few
flowsets and one stage:

```bash
printf 'flowset_%04d\n' 1 2 > first.txt
mkdir -p /tmp/first && ln -s "$PWD/output/flowsets" /tmp/first/flowsets
OUTPUT_DIR=/tmp/first RBS=135 ./scripts/run_paper_experiment.sh \
    -j 2 --stages sweep --flowsets first.txt
```

- `--flowsets first.txt` runs flowsets `0001` and `0002` only; `--stages
  sweep` runs only the sweep (no build, RQ4 or analysis).
- `RBS=135` runs only the 135 RB budget (an override meant for tests).
- `OUTPUT_DIR=/tmp/first` keeps the run out of the repository's `output/`; it
  needs a `flowsets/` folder, here a link to the committed flowsets.
- `-j 2` runs two workers, so at most two `siren` processes, and two license
  tokens, at a time.

Even so, a flowset has a median of 1200 candidate tuples and at most 11 000
(as `check_catalogue.py` prints them), each allowed up to 60 s: a first run
can take hours. Each pair's status is in
`/tmp/first/sweep/rb135/.parallel/status/` (`ok` when done).

## Look at the results

```text
<OUTPUT_DIR>/
├── sweep/rb135/reports/<method>/flowset_000N/
│   ├── combo_*.json                 # schedule tables (dispatch tables)
│   ├── planner_report.json          # ilp only: the candidate configurations
│   └── offline_phase_report.json    # ilp only: verdict over the candidate sweep
├── sweep/rb135/logs/<method>/flowset_000N.log   # what each method printed
└── paper_run/                       # manifest_<host>.txt, pairs_<host>.tsv
```

Reduce the reports to one verdict per (method, flowset):

```bash
python3 analysis/collect_verdicts.py \
    --reports /tmp/first/sweep/rb135/reports --flowsets output/flowsets \
    --mcs input/mcs_configurations.json --out verdicts.csv
```

It writes the CSV and prints a method × verdict summary. The verdict
vocabulary is defined in [Analysis](analysis/index.md#the-verdict-table).

## Next steps

- [Architecture](architecture.md): what the ILP proves, what a baseline
  simulates, and why the two are compared the way they are.
- [Scripts](scripts.md): `run_paper_experiment.sh` in full.
- [Analysis](analysis/index.md): from reports to the paper's table, figures
  and macros.
- `RUN_EXPERIMENTS.md`: the paper's runs, step by step.
