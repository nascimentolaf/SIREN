#ifndef SIREN_SCHEDULER_HPP
#define SIREN_SCHEDULER_HPP

#include <gurobi_c.h>
#include <nlohmann/json.hpp>

#include <iostream>
#include <fstream>
#include <sstream>
#include <vector>
#include <string>
#include <numeric>
#include <chrono>
#include <cmath>
#include <algorithm>
#include <filesystem>

using json = nlohmann::json;
using Clock = std::chrono::steady_clock;

// scheduler.cpp
//
// Time-indexed ILP formulation for periodic parallel-machine scheduling
// with a global resource-block constraint, as specified in:
//   "An Optimized Time-Indexed ILP Formulation for Periodic Parallel
//    Machine Scheduling with Resource Constraints"
//
// Input : planner_report.json  (parameters, applications, segmentation_options,
//                               mbc_combinations)
// Output: the path given to --output-scheduling (offline_phase_report.json),
//         or stdout
//
// Build (see CMakeLists.txt; GUROBI_HOME is the linux64 directory):
//   g++ -std=c++17 -O2 main.cpp scheduler.cpp -o siren \
//       -I$GUROBI_HOME/include -L$GUROBI_HOME/lib -lgurobi130
//

// --------------------------------------------------------------------------
// Config / CLI
// --------------------------------------------------------------------------
struct Config
{
    std::string input_path;
    std::string output_prefix = "";
    bool have_output = false;
    std::string objective = "feasibility"; // "feasibility" | "rb_peak" | "harq"
    // How the rb_peak objective searches the candidate tuples.
    //   "full"    : every candidate is solved with the rb_peak objective.
    //   "bounded" : pass 1 decides every candidate with the feasibility
    //               objective; pass 2 re-solves the schedulable ones with the
    //               rb_peak objective in ascending order of their mean RB usage
    //               over the hyperperiod, and stops as soon as the best peak
    //               found is <= ceil(mean) of the next candidate, since no
    //               schedule's peak can be below its mean usage.
    std::string rb_peak_search = "full";
    bool quiet = false;
    int n_rb = 50;                  // Bmax : global resource-block capacity per time unit
    int m_harq = 2;                 // number of identical processors (per user's CLI convention)
    double timeout_seconds = 300.0; // 5-minute global timeout (spec section 7)
    // Per-candidate cap. The global budget above bounds the whole sweep of one
    // flow set; this one bounds a single (segmentation, mkb) candidate, so a
    // candidate the solver cannot settle is skipped and the sweep moves on to
    // the next one instead of taking the flow set down with it. 0 disables it
    // (a candidate then gets whatever is left of the global budget).
    double candidate_timeout_seconds = 0.0;
    double tti_ms = 0.25;                   // discrete time unit: one TTI (Table I)
    std::string schedule_dir = "schedules"; // per-candidate dispatch-table JSON files
    bool emit_schedules = true;
    // Verdict mode: stop the candidate sweep at the first schedulable candidate.
    // The flow set's verdict is the same as after a full sweep (schedulable as
    // soon as one candidate is); what is lost is the rest of the candidates
    // (and so the minimum peak). Set by --stop-at-first or SIREN_STOP_AT_FIRST=1
    // in the environment, so that a run can switch it on through the scripts
    // without editing them.
    bool stop_at_first = false;
    // Not an option: read from the planner report's parameters.t_up_ms, so the
    // windows are those of the parameters the configurations were planned with.
    double t_up_ms = 0.0;
};

// --------------------------------------------------------------------------
// Input model  (planner_report.json)
// --------------------------------------------------------------------------
struct MbcCombo
{
    int b = 0;
    double c = 0.0; // C = A t_tx, ms: how long a TB holds its HARQ process and RBs
    int m = 0;      // MCS index, informational only -- not used by the ILP (per spec)
    int a = 0;      // A_i(K), attempts, informational only
};

struct SegOption
{
    int num_segments = 0;
    int payload_per_segment = 0;
    double tb_error = 0.0;
    int tbs = 0;
    std::vector<MbcCombo> mbc_combinations;
};

struct AppSpec
{
    std::string app;
    bool feasible = true; // planner verdict: false when P_i is empty (Eq. (6))
    double deadline = 0.0;
    double error = 0.0;
    int packet_size = 0;
    double period = 0.0;
    std::string type;
    std::vector<SegOption> segmentation_options;
};

// --------------------------------------------------------------------------
// A resolved per-application choice: one (segmentation_option, mkb_combo) pair.
// This fixes T_i, D_i, C_i, B_i, N_i for the flow, per section 7 of the spec.
// --------------------------------------------------------------------------
// Times are carried in TTIs -- the discrete time unit of Table I -- because
// that is the granularity at which the RB pool is replenished and a TB may
// start. The _ms fields are kept alongside purely for reporting.
struct ChosenTask
{
    std::string app;
    long long period = 0;   // T_i, TTIs
    long long deadline = 0; // D_i, TTIs: the packet's relative deadline
    long long c = 0;        // C_i, TTIs: HARQ process and RBs held by one TB
    long long t_up = 0;     // t_up, TTIs: after its last TB ends, a packet still
                            // spends t_up in the other layers, holding neither
                            // HARQ process nor RBs; it must be delivered by D_i
    long long b = 0;        // B_i (resource blocks per TTI)
    int num_segments = 0;   // N_i (number of identical subtasks / transport blocks)
    int m = 0;              // MCS index, carried through for reporting only
    int tbs = 0;
    int attempts = 0; // A_i(K), reporting only
    double period_ms = 0.0;
    double deadline_ms = 0.0;
    double c_ms = 0.0;
};

// All (segOptIdx, mbcIdx) choices flattened per app, for cross-product enumeration.
struct AppChoice
{
    int segIdx;
    int mbcIdx;
};

// --------------------------------------------------------------------------
// Job expansion (section 1.2) + ILP construction & solve (sections 2-5)
// --------------------------------------------------------------------------
struct Job
{
    int appIdx; // index into the candidate's task vector
    long long release;
    long long deadlineAbs; // the packet's absolute deadline
    long long latestEnd;   // deadlineAbs - t_up: the TB must end by then
    long long C;
    long long B;
    int releaseGroup; // (appIdx, repetition r) group id, for symmetry breaking
    int subtaskIdx;   // order within the release group (a = 0..N_i-1)
};

struct ScheduleEntry
{
    std::string app;
    int subtask = 0; // segment index a (0..N_i-1) within its release
    int machine = 0;
    long long release = 0;  // TTIs
    long long start = 0;    // TTIs
    long long end = 0;      // TTIs
    long long deadline = 0; // absolute deadline, TTIs
    long long b = 0;        // resource blocks held during [start, end)
};

struct SolveResult
{
    bool schedulable = false;
    bool trivially_infeasible = false;           // some job's window is empty (C + t_up > D), no solver call needed
    bool constrained_deadline_violation = false; // some D > T, outside the model's feasibility interval
    bool timed_out = false;
    // Gurobi neither proved the model infeasible nor found a table, nor ran out
    // of time: an error, or a status this code does not read as a proof.
    // Undecided, exactly like a timeout -- never counted as unschedulable.
    bool error = false;
    std::string status; // Gurobi's optimization status, as text (empty for the fast paths)
    long long hyperperiod = 0;
    long long num_jobs = 0;
    long long num_variables = 0;
    long long num_constraints = 0;
    double solve_time_ms = 0.0;
    int harq_used = -1;                  // only meaningful for objective == harq
    int rb_peak_used = -1;               // only meaningful for objective == rb_peak
    std::vector<ScheduleEntry> schedule; // populated only when schedulable
};

// --------------------------------------------------------------------------
// Minimal row-wise model builder.
//
// The formulation below calls four mutators; the model is accumulated in
// plain vectors and handed to Gurobi in two calls, GRBnewmodel() for the
// columns and GRBXaddconstrs() for every row at once, rather than row by row.
//
// A row is a sense and a right-hand side, the way Gurobi takes it: every row
// the formulation builds is either "= rhs" or "<= rhs". Each addRow() call
// passes distinct column indices (true of every row the formulation builds).
// --------------------------------------------------------------------------
struct ModelBuilder
{
    std::vector<double> colCost;
    std::vector<double> colLower;
    std::vector<double> colUpper;
    std::vector<char> colType;

    std::vector<char> rowSense;
    std::vector<double> rowRhs;
    std::vector<size_t> rowStart{0}; // row-wise CSR: numRows()+1 entries
    std::vector<int> rowIndex;
    std::vector<double> rowValue;

    // Columns may be touched in any order; grow the arrays to fit.
    void ensureColumn(int col)
    {
        if (static_cast<int>(colCost.size()) <= col)
        {
            colCost.resize(col + 1, 0.0);
            colLower.resize(col + 1, 0.0);
            colUpper.resize(col + 1, GRB_INFINITY);
            colType.resize(col + 1, GRB_CONTINUOUS);
        }
    }

    void setColumnBounds(int col, double lo, double up)
    {
        ensureColumn(col);
        colLower[col] = lo;
        colUpper[col] = up;
    }

    void setInteger(int col)
    {
        ensureColumn(col);
        colType[col] = GRB_INTEGER;
    }

    void setObjective(int col, double v)
    {
        ensureColumn(col);
        colCost[col] = v;
    }

    // sense: GRB_EQUAL or GRB_LESS_EQUAL
    void addRow(int n, const int *cols, const double *vals, char sense, double rhs)
    {
        for (int k = 0; k < n; ++k)
        {
            rowIndex.push_back(cols[k]);
            rowValue.push_back(vals[k]);
        }
        rowSense.push_back(sense);
        rowRhs.push_back(rhs);
        rowStart.push_back(rowIndex.size());
    }

    int numColumns() const { return static_cast<int>(colCost.size()); }
    int numRows() const { return static_cast<int>(rowSense.size()); }

    // Builds the model in env (objective sense: minimise, Gurobi's default).
    // Returns Gurobi's error code, 0 on success; on failure *modelP is null.
    // The arrays are copied by Gurobi, and the C API takes them as non-const.
    int loadInto(GRBenv *env, GRBmodel **modelP)
    {
        *modelP = nullptr;
        int err = GRBnewmodel(env, modelP, "siren", numColumns(), colCost.data(),
                              colLower.data(), colUpper.data(), colType.data(), nullptr);
        if (err)
            return err;
        if (numRows() > 0)
            err = GRBXaddconstrs(*modelP, numRows(), rowIndex.size(), rowStart.data(),
                                 rowIndex.data(), rowValue.data(), rowSense.data(),
                                 rowRhs.data(), nullptr);
        if (err)
        {
            GRBfreemodel(*modelP);
            *modelP = nullptr;
        }
        return err;
    }
};

// The process's Gurobi environment, started once by startSolver() and reused
// by every model: starting an environment checks out a license (a WLS token
// for a Web License Service license), which is too slow to repeat per
// candidate. startSolver() returns false, with the reason in `error`, when no
// license can be obtained; the caller exits then, since no candidate could be
// decided.
bool startSolver(std::string &error);
void stopSolver();
// "gurobi <major>.<minor>.<technical>", the library's version: every report
// records the solver that produced it.
std::string solverName();

void printUsage(const char *prog);
bool parseArgs(int argc, char **argv, Config &cfg);

// Also returns parameters.t_up_ms, which the report must carry: the ILP has no
// default for it, so a report without it is refused rather than scheduled as
// if the upper-layer delay were zero.
std::vector<AppSpec> loadPlannerReport(const std::string &path, double &tUpMs);

// Milliseconds to TTIs. The three helpers round in the direction that cannot
// make an infeasible task set look feasible: a period must land exactly on the
// grid (the hyperperiod is meaningless otherwise), a deadline is truncated
// towards less slack, and an execution time is rounded up.
long long periodToTicks(double ms, double tti_ms, const std::string &what);
long long deadlineToTicks(double ms, double tti_ms);
long long wcetToTicks(double ms, double tti_ms);

ChosenTask resolveChoice(const AppSpec &app, const AppChoice &c, double tti_ms, double t_up_ms);

long long gcdll(long long a, long long b);
long long lcmll(long long a, long long b);

SolveResult solveCandidate(const std::vector<ChosenTask> &tasks, int numHARQ, int Bmax, const std::string &objective, double remainingBudgetSeconds);

#endif