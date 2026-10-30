#!/bin/bash
#
# Central configuration for the end-to-end experiment scripts.
#
# This file is *sourced*, never executed. It therefore must not change the
# caller's working directory (the phase scripts and the
# baseline_algorithms/Makefile all source it from different places) and must
# not leave a non-zero exit status behind.

# ------------------------------------------------------------------------
# Paths
#
# Everything is derived from this file's own location instead of from $PWD,
# so the scripts can be launched from any working directory.
# ------------------------------------------------------------------------
__parameters_sh_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export SCRIPTS_DIR="$__parameters_sh_dir"
export DIR="$(cd "$__parameters_sh_dir/.." && pwd)" # the repository root

unset __parameters_sh_dir

# Paths given in the environment may be relative. They are made absolute
# here, against the directory the run was launched from: every binary is run
# from its own directory, and would otherwise resolve them against that.
# run_paper_experiment.sh and sweep_experiment.sh cd into
# scripts/ before sourcing this file, so they export LAUNCH_DIR first;
# scripts that source it without moving need nothing.
__parameters_sh_abs() {
    case "$1" in
        /*) printf '%s' "$1" ;;
        *) printf '%s/%s' "${LAUNCH_DIR:-$PWD}" "$1" ;;
    esac
}

export INPUT_DIR="$DIR/input"
export APP_DATABASE="$(__parameters_sh_abs "${APP_DATABASE:-$INPUT_DIR/app_database.json}")"
export MCS_CONFIG="$(__parameters_sh_abs "${MCS_CONFIG:-$INPUT_DIR/mcs_configurations.json}")"

export FLOWSET_GEN_DIR="$DIR/offline_phase/generate_flowset"
export PLANNER_DIR="$DIR/offline_phase/planning"
export ILP_EVALUATION_DIR="$DIR/offline_phase/ilp"
export BASELINE_ALGORITHMS_DIR="$DIR/baseline_algorithms"

export ONLINE_PHASE_DIR="$DIR/online_phase"
export PERFORMANCE_METRICS_DIR="$ONLINE_PHASE_DIR/performance_metrics"

# Overridable so several runs can coexist: everything below is derived from
# it, so pointing OUTPUT_DIR at a private directory gives a run its own
# flowsets/reports/logs tree.
export OUTPUT_DIR="$(__parameters_sh_abs "${OUTPUT_DIR:-$DIR/output}")"
unset -f __parameters_sh_abs
export FLOWSETS_DIR="$OUTPUT_DIR/flowsets"
export REPORTS_DIR="$OUTPUT_DIR/reports"        # offline: reports/<method>/<flowset>/
export LOGS_DIR="$OUTPUT_DIR/logs"              # offline: logs/<method>/<flowset>.log
export ONLINE_OUTPUT_DIR="$OUTPUT_DIR/online_phase" # online: online_phase/<method>/<flowset>/
export ONLINE_LOGS_DIR="$ONLINE_OUTPUT_DIR/logs"    # online: logs/<method>/<flowset>.log

# ------------------------------------------------------------------------
# Which scheduling methods to evaluate
#
# "ilp" is the offline time-indexed ILP scheduler; the rest are the
# baseline algorithms, which all share one CLI contract:
#
#   ./<method> <flowset.json> <mcs_configurations.json> <output.json>
#
# and all emit the same combo-style dispatch-table JSON, so the online
# phase plots every method through the same path.
#
# Override from the environment to run a subset, e.g.
#   METHODS="ilp must" ./run_paper_experiment.sh
# ------------------------------------------------------------------------
export METHODS="${METHODS:-ilp rr pf qospf must}"

# ------------------------------------------------------------------------
# Method variants
#
# A variant runs an existing baseline with some variables changed, under a
# name of its own: its results land in reports/<variant>/ beside the plain
# method's, and every script and analysis treats it as one more method. It
# runs only when METHODS names it. Each entry is
#
#   <variant>=<baseline>[:<VAR>=<value>[,<VAR>=<value>...]]
#
#   5gtps_harq  5G-TPS with HARQs=auto: one HARQ process per TB job of the
#               hyperperiod, so the process count never limits it -- the
#               assumption of the 5G-TPS paper, which has no HARQ dimension.
#               Plain 5gtps gets HARQs processes, like every other method.
#
# e.g.  METHODS=5gtps_harq RESUME=0 ./sweep_experiment.sh -j 12
# ------------------------------------------------------------------------
export METHOD_VARIANTS="${METHOD_VARIANTS:-5gtps_harq=5gtps:HARQs=auto}"

# ------------------------------------------------------------------------
# Radio / real-time system configuration
# ------------------------------------------------------------------------
# Every value here is overridable from the environment for one-off runs,
# e.g.  NUMBER_OF_APPSETS=20 python3 check_catalogue.py
export APP_PER_SET="${APP_PER_SET:-4}"                    # flows per flowset (paper: 4)
export NUMBER_OF_APPSETS="${NUMBER_OF_APPSETS:-1000}"      # flowsets drawn from C(30,4) (paper: 1000)
export FLOWSET_SEED="${FLOWSET_SEED:-20260922}"             # generate_flowset shuffle seed; empty = random
export RBs="${RBs:-135}"                                 # 100 MHz FR1 carrier at 60 kHz SCS (TS 38.101-1)
export HARQs="${HARQs:-3}"
# Time units: TTI, UPPER_LAYER_DELAY and FEEDBACK_PER_RET are in SECONDS here
# (the binaries read them so); the catalogue (period, deadline), the planner's
# C and every report field named *_ms are in MILLISECONDS; fields named *_tti
# count TTIs. With TTI = 0.25 ms, 1 ms = 4 TTIs, and every catalogue time is a
# whole number of ms (scripts/check_catalogue.py enforces it), so ms <-> TTI
# conversions are exact.
export TTI="${TTI:-0.00025}"                             # seconds (0.25 ms, 60 kHz SCS)
# t_up: time a packet spends in the other layers (UE + gNB processing). It
# holds neither a HARQ process nor RBs, but counts towards the packet's
# deadline: a TB holds its process and RBs for C = A * (TTI + FEEDBACK_PER_RET)
# and must end t_up before the deadline.
export UPPER_LAYER_DELAY="${UPPER_LAYER_DELAY:-0.004}"   # seconds
export SINR_DB="${SINR_DB:-10}"                          # dB; selects the MCS lookup table

# RLC header carried by every segment, in bytes. Single source of truth: the
# offline planner, every baseline scheduler (through
# baseline_algorithms/common) and scripts/check_catalogue.py read this one
# variable, so changing it here changes the segmentation everywhere at once.
export RLC_HEADER_BYTES="${RLC_HEADER_BYTES:-8}"

# A^max: the layer-wide cap on HARQ transmission attempts (Section IV-B).
# A configuration needing more attempts than this to reach its Eq. (3)
# per-TB target is discarded as infeasible.
export A_MAX="${A_MAX:-4}"
export FEEDBACK_PER_RET="${FEEDBACK_PER_RET:-$(echo "3 * $TTI" | bc -l)}" # seconds (k1)

# The same two values under camelCase names. Nothing in this repository reads
# them any more: every baseline, the planner and the ILP read FEEDBACK_PER_RET
# and UPPER_LAYER_DELAY. They are kept only for older scripts that might still
# use the camelCase spellings.
export feedbackPerRet="$FEEDBACK_PER_RET"
export upperLayerDelay="$UPPER_LAYER_DELAY"

# ------------------------------------------------------------------------
# QoS-PF utility weights and throughput EMA.
#
# alpha / beta / gamma weight the delay-urgency, GBR-deficit and priority
# terms of U_i(t). The defaults are the paper's "Balanced (Default)"
# configuration; its other two published settings are
#
#   Delay-Tuned    ALPHA=0.7 BETA=0.2 GAMMA=0.1
#   Fairness-Tuned ALPHA=0.2 BETA=0.2 GAMMA=0.6
#
# which can be swept from the environment, e.g.
#   ALPHA=0.7 BETA=0.2 GAMMA=0.1 ./run_paper_experiment.sh
#
# PF_EMA_LAMBDA is the weight of the newest sample in the throughput average
# R_bar. The paper gives none; its released implementation (Simu5G's PF
# scheduler, MohamedSeliem/Simu5G pull request 1) uses Simu5G's pfAlpha,
# 0.95 by default and not overridden in its configurations.
# ------------------------------------------------------------------------
export ALPHA="${ALPHA:-0.4}"
export BETA="${BETA:-0.3}"
export GAMMA="${GAMMA:-0.3}"
export PF_EMA_LAMBDA="${PF_EMA_LAMBDA:-0.95}"

# ------------------------------------------------------------------------
# Proportional-fair averaging filter (rr/pf).
#
# PF_ALPHA is 1/t_c in R_i(t) = (1 - 1/t_c) R_i(t-1) + (1/t_c) r_i(t): the
# weight given to the newest per-TTI sample, so 1/PF_ALPHA is the filter
# memory in TTIs. At the default 0.01 that
# memory is 100 TTIs = 25 ms, about a fifth of a 120 ms hyperperiod and in
# the classic PF range; raise PF_ALPHA for a shorter horizon.
# ------------------------------------------------------------------------
export PF_ALPHA="${PF_ALPHA:-0.01}"
export PF_INITIAL_THROUGHPUT="${PF_INITIAL_THROUGHPUT:-1.0}" # bytes/ms

# ------------------------------------------------------------------------
# ILP evaluation
#
# ILP_TIMEOUT is a wall-clock budget for the *whole* candidate sweep of one
# flowset, not per candidate. The planner can emit hundreds of thousands of
# (segmentation, mkb) combinations for a 5-app flowset, so a short budget
# means the sweep reports "timed_out" with whatever it found so far.
#
# ILP_CANDIDATE_TIMEOUT is the per-candidate cap: a single (segmentation, mkb)
# candidate that does not settle within it is recorded as timed out, skipped,
# and the sweep goes on to the next candidate. Without it one pathological
# candidate burns the entire ILP_TIMEOUT and takes the rest of the flowset's
# candidates with it. Set it to 0 to disable the cap (a candidate then gets
# whatever is left of ILP_TIMEOUT, the pre-existing behaviour).
# ------------------------------------------------------------------------
export ILP_OBJECTIVE="${ILP_OBJECTIVE:-feasibility}"          # feasibility | rb_peak | harq
# rb_peak only. "bounded": decide every candidate with the feasibility
# objective, then minimise the peak over the schedulable ones in ascending
# mean-RB order and stop at the bound (a peak is never below the mean usage).
# "full": minimise the peak on every candidate.
export ILP_RB_PEAK_SEARCH="${ILP_RB_PEAK_SEARCH:-bounded}"    # bounded | full
export ILP_TIMEOUT="${ILP_TIMEOUT:-900}"                      # seconds, whole sweep
export ILP_CANDIDATE_TIMEOUT="${ILP_CANDIDATE_TIMEOUT:-60}"   # seconds, one candidate (0 = off)

# ------------------------------------------------------------------------
# Flowset source
#
# 0           : generate a fresh sample of $NUMBER_OF_APPSETS flowsets of
#               $APP_PER_SET applications each.
# 1 (default) : reuse whatever is already in $FLOWSETS_DIR, and ignore
#               NUMBER_OF_APPSETS / APP_PER_SET.
#
# Reuse is what you want when comparing methods, sweeping a radio parameter,
# or re-running after a code change: the generator shuffles and re-samples on
# every run, so regenerating would change the inputs underneath the
# comparison. It also keeps the flowsets committed under output/flowsets
# intact, which is the only part of output/ that is tracked.
# ------------------------------------------------------------------------
export REUSE_FLOWSETS="${REUSE_FLOWSETS:-1}"

# ------------------------------------------------------------------------
# Housekeeping
#
# The flowset generator numbers its files from 0001 on every run, so stale
# flowsets from a previous run would otherwise be silently re-processed and
# mixed into this run's results. Reports and logs are always cleared when
# this is set; the flowsets themselves are kept when REUSE_FLOWSETS=1, since
# deleting the inputs you asked to reuse is never what was meant.
# ------------------------------------------------------------------------
export CLEAN_OUTPUT="${CLEAN_OUTPUT:-1}"

true
