# SIREN

Code and data of the paper *SIREN: Table-Driven Real-Time MAC Scheduler*, by
Alaf Nascimento, Samuel Tardieu, Philippe Martins and Laurent Pautet (LTCI,
Télécom Paris, Institut Polytechnique de Paris).

**SIREN** (Scheduling with ILP for Real-time, Error-bounded New Radio) is a
table-driven real-time MAC scheduler for periodic URLLC downlink flows in a
private 5G gNB. Each flow is turned into real-time tasks, one per transport
block, over a three-dimensional resource grid: resource blocks × HARQ
processes × time. An offline, time-indexed ILP, solved with
[Gurobi](https://www.gurobi.com), computes a cyclic schedule table that
provably meets every deadline over one hyperperiod, under HARQ
retransmissions and an RB budget. The paper compares it with four baselines:
MUST, QoS-PF, RR and PF.

**Naming.** In the code, the reports and the CSVs, SIREN is the method `ilp`,
solved by the program `siren` (`offline_phase/siren/`); the
baselines are `must`, `qospf`, `rr` and `pf`.

## Contents

```text
common/                 shared configuration selection (Section V) and its unit test
input/                  application catalogue, MCS/BLER table
offline_phase/          flowset generation, configuration planning, siren (the ILP, Gurobi)
baseline_algorithms/    the four baselines: MUST, QoS-PF, RR, PF
analysis/               verdict tables, the paper's tables, figures and macros, RQ4, the search
bler/                   link-level BLER simulation that produced the MCS table
scripts/                build, the paper's experiments (run_paper_experiment.sh), catalogue check
output/flowsets/        the 1000 input flowsets
results/                the paper's results: CSVs, tables, figures, macros, RQ4 models
doc/                    documentation (Markdown), and doc/html/ (generated)
```

## Documentation

All documentation is in [`doc/`](doc/index.md), as Markdown, and as a
navigable website: open `doc/html/index.html` in a browser (it works offline,
straight from disk). Rebuild the HTML after editing the Markdown with
`python3 doc/build_docs.py` (needs `pip install markdown`).

| Start with | For |
| :--- | :--- |
| [Getting started](doc/getting_started.md) | prerequisites, build, a first run |
| [`RUN_EXPERIMENTS.md`](RUN_EXPERIMENTS.md) | reproducing the paper's results, step by step |
| [`results/README.md`](results/README.md) | the paper's results, and where each of its numbers comes from |
| [Architecture](doc/architecture.md) | how the pieces fit and what each method's verdict means |
| [ILP evaluation](doc/offline_phase/evaluation.md) | `siren`: the model, its build, its Gurobi license and settings |
| [Analysis](doc/analysis/index.md) | from reports to the paper's tables, figures and macros |
| [Known issues](doc/known_issues.md) | what is inconsistent or easy to trip over |

## Requirements

- **Gurobi 13.0** (the paper's runs used 13.0.1) with its C API, from
  Gurobi's own distribution (the `gurobipy` wheel from pip is not enough),
  and `GUROBI_HOME` set to its `linux64` directory. The repository ships
  neither Gurobi nor a license.
- **A Gurobi license** of your own. Each `siren` process takes one license
  token for its whole run, so the workers of all machines together must stay
  within the number of processes the license allows at once.
- CMake ≥ 3.13, `make`, and `g++` with C++20 support (`siren` and the
  baselines are C++17; the flowset generator and the planner ask for C++20).
- `nlohmann/json` (`nlohmann-json3-dev` on Debian/Ubuntu); `scripts/install.sh`
  installs it if CMake cannot find it.
- Linux with Bash and the tools `scripts/run_paper_experiment.sh` calls:
  `lscpu`, `taskset` and `flock` (util-linux), `bc`, and `git` (for the
  commit it records).
- Python 3 with `numpy`, `pandas`, `matplotlib` and `scipy` (the analysis).
- For the link-level BLER simulation only: the packages of
  `bler/requirements.txt` (Sionna 1.x, TensorFlow).
- For the documentation only: the `markdown` package.

## Reproducing the paper

```bash
export GUROBI_HOME=/path/to/gurobi1301/linux64    # and a Gurobi license
(cd scripts && ./install.sh)                       # build
make -C common test                                # unit test of the configuration selection
python3 scripts/check_catalogue.py                 # validate the application catalogue
./scripts/run_paper_experiment.sh -j <workers>     # sweep, RQ4, search, tables, figures and macros
```

`run_paper_experiment.sh` runs the RB-budget sweep (SIREN and the four
baselines on the 1000 flowsets at 51, 79, 107 and 135 RBs; for SIREN, the
`rb_peak` objective with the bounded search, 60 s per ILP tuple, one Gurobi
thread, a zero optimality gap, `M = 3`), then RQ4 (every flowset SIREN
schedules at 135 RBs, its minimum-peak tuple at five hyperperiods, 1 h per
model, 12 models at a time), then the search (each baseline run on every tuple
of every flowset at every budget, an ablation of SIREN), then writes the
paper's tables, figures and macros into `analysis/figures/paper/`. The run is
long: the 1000 flowsets have a median of 1260 ILP tuples and at most 11 000,
each allowed up to 60 s.
[`RUN_EXPERIMENTS.md`](RUN_EXPERIMENTS.md) gives every step, how to resume, run
some stages or flowsets, or share the run between machines, and how to compare
the outputs with the paper's.

## The paper's results

[`results/`](results/README.md) holds the results of the paper's run: the
CSVs, Tables IV and V, the figures, the macros that hold every number of the
Performance Evaluation section, and the inputs of the RQ4 models. Its README
says what each file holds, where each kind of number comes from, and how to
regenerate the macros and the tables from the CSVs, without Gurobi.

A new run writes into `output/` and `analysis/`, which git ignores, and leaves
`results/` as it is; step 6 of [`RUN_EXPERIMENTS.md`](RUN_EXPERIMENTS.md#6-the-outputs)
says how to compare the two.

## Contributors

| Role | Contributor |
| :--- | :--- |
| PhD student | Alaf Nascimento |
| Advisor | Laurent Pautet |
| Co-advisor | Philippe Martins |
| Co-advisor | Samuel Tardieu |

LTCI, Télécom Paris, Institut Polytechnique de Paris. Each author can be
reached at `firstname.lastname@telecom-paris.fr`.

## Funding

This work was supported by the France 2030 ANR (Agence nationale de la
recherche) project "PEPR Future Networks" (reference ANR-22-PEFT-0003).

## License

**All rights reserved** (see `LICENSE`): no licence has been granted yet, so
the code may be read but not copied, modified or redistributed without the
authors' permission. Third-party files keep their own licences.
