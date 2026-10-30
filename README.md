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
analysis/               verdict tables, the paper's table, figures and macros, RQ4
bler/                   link-level BLER simulation that produced the MCS table
scripts/                build, the paper's experiments (run_paper_experiment.sh), catalogue check
output/flowsets/        the 1000 input flowsets
doc/                    documentation (Markdown), and doc/html/ (generated)
```

`results/` will hold the paper's results: the CSVs, table, figures and macros
its run produced.

## Documentation

All documentation is in [`doc/`](doc/index.md), as Markdown, and as a
navigable website: open `doc/html/index.html` in a browser (it works offline,
straight from disk). Rebuild the HTML after editing the Markdown with
`python3 doc/build_docs.py` (needs `pip install markdown`).

| Start with | For |
| :--- | :--- |
| [Getting started](doc/getting_started.md) | prerequisites, build, a first run |
| [`RUN_EXPERIMENTS.md`](RUN_EXPERIMENTS.md) | reproducing the paper's results, step by step |
| [Architecture](doc/architecture.md) | how the pieces fit and what each method's verdict means |
| [ILP evaluation](doc/offline_phase/evaluation.md) | `siren`: the model, its build, its Gurobi license and settings |
| [Analysis](doc/analysis/index.md) | from reports to the paper's table, figures and macros |
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
- Python 3 with `numpy`, `pandas` and `matplotlib` (the analysis).
- For the link-level BLER simulation only: the packages of
  `bler/requirements.txt` (Sionna 1.x, TensorFlow).
- For the documentation only: the `markdown` package.

## Reproducing the paper

```bash
export GUROBI_HOME=/path/to/gurobi1301/linux64    # and a Gurobi license
(cd scripts && ./install.sh)                       # build
make -C common test                                # unit test of the configuration selection
python3 scripts/check_catalogue.py                 # validate the application catalogue
./scripts/run_paper_experiment.sh -j <workers>     # sweep, RQ4, table, figures and macros
```

`run_paper_experiment.sh` runs the RB-budget sweep (SIREN and the four
baselines on the 1000 flowsets at 51, 79, 107 and 135 RBs, 60 s per ILP
tuple), then RQ4, then writes the paper's table, figures and macros into
`analysis/figures/paper/`. The run is long: a flowset has a median of 1200 ILP
tuples and at most 11 000 (as `check_catalogue.py` prints them), each allowed
up to 60 s. [`RUN_EXPERIMENTS.md`](RUN_EXPERIMENTS.md)
gives every step, how to resume, run some stages or flowsets, or share the
run between machines, and how to compare the outputs with the paper's.

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
