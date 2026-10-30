# siren: the time-indexed ILP of SIREN, solved with Gurobi

`siren` computes SIREN's schedule tables. It reads the planner's report of a
flowset (`planner_report.json`), builds one time-indexed ILP per candidate
tuple of configurations, solves it with Gurobi 13.0 through its C API, and
writes a dispatch table for every schedulable candidate and a report of the
whole sweep. In the reports and CSVs, SIREN is the method `ilp`.

The model, the options, the sweep and the report are documented in
[`doc/offline_phase/evaluation.md`](../../doc/offline_phase/evaluation.md);
the formulation is written up in LaTeX in `src/latex/`.
[`scripts/run_paper_experiment.sh`](../../scripts/run_paper_experiment.sh)
runs it for the paper's experiments.

## Build

Gurobi ships neither a CMake package nor a pkg-config file. `GUROBI_HOME`
is the `linux64` directory of the Gurobi 13.0 distribution
(`gurobi13.0.1_linux64.tar.gz` from Gurobi's download site, unpacked), which
holds `include/gurobi_c.h` and `lib/libgurobi130.so`. The build also needs
CMake, a C++17 compiler and nlohmann/json:

```bash
export GUROBI_HOME=/path/to/gurobi1301/linux64
make                         # CMake build -> ./siren
```

Without them, CMake stops with `Gurobi 13.0 not found under GUROBI_HOME=...`.
The build tree's RPATH points at `$GUROBI_HOME/lib`, so `./siren` runs without
`LD_LIBRARY_PATH`. The `gurobipy` wheel from pip is not enough: it has no C
headers, and its license is refused outside `gurobipy`.

## License

Building needs no license; running does. Each run starts one Gurobi
environment, and so checks out one license token, for its whole sweep.
Gurobi looks for the license in `GRB_LICENSE_FILE`, then in its default
places such as `~/gurobi.lic`. A run that gets no license exits with status 1
before solving anything (`Cannot start Gurobi: ...`), rather than reporting
every candidate as a solver error.

A license that limits how many processes may hold a token at once limits how
many `siren` processes may run in parallel, over every machine that uses it:
a run started beyond the limit gets no license and exits with status 1.

A Web License Service (WLS) license file holds a private access ID and secret:
keep it out of the repository (`.gitignore` ignores every `gurobi.lic`).

## Run

```bash
export GRB_LICENSE_FILE=/path/to/gurobi.lic
./siren planner_report.json --n-rb 135 --m-harq 3 --tti 0.25 \
        --objective rb_peak --rb-peak-search bounded \
        --timeout 0 --candidate-timeout 60 \
        --output-scheduling report.json --schedule-dir tables/
```

These are the settings of the paper's runs (`run_paper_experiment.sh`).
`./siren` without arguments prints its options. `SIREN_STOP_AT_FIRST=1` in
the environment has the effect of `--stop-at-first`.

## Solver settings and outcomes

Set once on the environment, so every model inherits them:

| Parameter | Value | Why |
|---|---|---|
| `OutputFlag` | 0 | no log on stdout, where the report goes without `--output-scheduling` |
| `Threads` | 1 | the experiments run one process per core |
| `MIPGap`, `MIPGapAbs` | 0 | every objective is an integer: the optimum is closed exactly |
| `TimeLimit` | per candidate | what is left of `--timeout`, or `--candidate-timeout`, at least 1 s |

| Gurobi status | Verdict of the candidate |
|---|---|
| `OPTIMAL` | schedulable: the table is read from the variables at 1 (value > 0.5) |
| `INFEASIBLE`, `INF_OR_UNBD` | not schedulable (every variable is bounded, so the model cannot be unbounded) |
| `TIME_LIMIT`, `ITERATION_LIMIT` | timed out: undecided, even if a feasible solution was found |
| anything else, or an API error | solver error: undecided |

Every report records the library's version in `config.solver`, for example
`gurobi 13.0.1`.

`validate_schedule.py` re-checks dispatch tables against the ILP's
constraints, from their `*_tti` fields alone, without the solver:

```bash
python3 validate_schedule.py tables/combo_*.json
```
