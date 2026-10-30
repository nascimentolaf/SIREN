#include "scheduler.hpp"
#include <cstdlib>

void printUsage(const char *prog)
{
    std::cerr << "Usage: " << prog << " <planner_report.json> [options]\n"
                                      "Options:\n"
                                      "  --objective {feasibility|rb_peak|harq}\n"
                                      "                                       optimization objective (default: feasibility)\n"
                                      "                                         feasibility : any schedule satisfying all constraints\n"
                                      "                                         rb_peak     : minimize the peak resource-block usage\n"
                                      "                                         harq        : minimize the number of HARQ processes used\n"
                                      "                                         ('machines' is accepted as an alias for 'harq')\n"
                                      "  --quiet, -q                          suppress detailed per-candidate output\n"
                                      "  --output-scheduling <path>           write the report to <path> (default: stdout)\n"
                                      "  --n-rb <int>                         max resource blocks per time unit (Bmax), default 50\n"
                                      "  --m-harq <int>                       number of identical processors (m), default 2\n"
                                      "  --timeout <seconds>                  global wall-clock budget for the whole sweep, default 300\n"
                                      "                                       (0 = no sweep budget, only --candidate-timeout applies)\n"
                                      "  --candidate-timeout <seconds>        per-candidate cap; a candidate that hits it is\n"
                                      "                                       skipped and the sweep continues (0 = off, default)\n"
                                      "  --tti <ms>                           discrete time unit, default 0.25 ms\n"
                                      "  --schedule-dir <dir>                 output dir for per-candidate dispatch tables, default 'schedules'\n"
                                      "  --no-schedules                       don't write per-candidate dispatch-table files\n"
                                      "  --rb-peak-search {full|bounded}      rb_peak only: solve every candidate (full, default)\n"
                                      "                                       or decide all with the feasibility objective, then\n"
                                      "                                       minimise the peak over the schedulable ones in\n"
                                      "                                       ascending mean-RB order, stopping at the bound\n"
                                      "  --stop-at-first                      verdict mode: stop at the first schedulable\n"
                                      "                                       candidate (also SIREN_STOP_AT_FIRST=1 in the\n"
                                      "                                       environment)\n";
}

bool parseArgs(int argc, char **argv, Config &cfg)
{
    if (argc < 2)
    {
        printUsage(argv[0]);
        return false;
    }
    cfg.input_path = argv[1];
    for (int i = 2; i < argc; ++i)
    {
        std::string a = argv[i];
        auto need = [&](const char *name) -> std::string
        {
            if (i + 1 >= argc)
            {
                std::cerr << "Missing value for " << name << "\n";
                std::exit(1);
            }
            return argv[++i];
        };
        if (a == "--objective")
        {
            cfg.objective = need("--objective");
            if (cfg.objective == "machines")
                cfg.objective = "harq"; // backwards-compatible alias
            if (cfg.objective != "feasibility" && cfg.objective != "rb_peak" && cfg.objective != "harq")
            {
                std::cerr << "Invalid --objective: " << cfg.objective << "\n";
                return false;
            }
        }
        else if (a == "--quiet" || a == "-q")
        {
            cfg.quiet = true;
        }
        else if (a == "--output-scheduling")
        {
            cfg.output_prefix = need("--output-scheduling");
            cfg.have_output = true;
        }
        else if (a == "--n-rb")
        {
            cfg.n_rb = std::stoi(need("--n-rb"));
        }
        else if (a == "--m-harq")
        {
            cfg.m_harq = std::stoi(need("--m-harq"));
        }
        else if (a == "--timeout")
        {
            cfg.timeout_seconds = std::stod(need("--timeout"));
            if (cfg.timeout_seconds < 0.0)
            {
                std::cerr << "--timeout must be >= 0 (0 disables the sweep budget)\n";
                return false;
            }
        }
        else if (a == "--candidate-timeout")
        {
            cfg.candidate_timeout_seconds = std::stod(need("--candidate-timeout"));
            if (cfg.candidate_timeout_seconds < 0.0)
            {
                std::cerr << "--candidate-timeout must be >= 0 (0 disables the per-candidate cap)\n";
                return false;
            }
        }
        else if (a == "--tti")
        {
            cfg.tti_ms = std::stod(need("--tti"));
            if (!(cfg.tti_ms > 0.0))
            {
                std::cerr << "--tti must be positive\n";
                return false;
            }
        }
        else if (a == "--schedule-dir")
        {
            cfg.schedule_dir = need("--schedule-dir");
        }
        else if (a == "--rb-peak-search")
        {
            cfg.rb_peak_search = need("--rb-peak-search");
            if (cfg.rb_peak_search != "full" && cfg.rb_peak_search != "bounded")
            {
                std::cerr << "Invalid --rb-peak-search: " << cfg.rb_peak_search << " (full|bounded)\n";
                return false;
            }
        }
        else if (a == "--no-schedules")
        {
            cfg.emit_schedules = false;
        }
        else if (a == "--stop-at-first")
        {
            cfg.stop_at_first = true;
        }
        else if (a == "--help" || a == "-h")
        {
            printUsage(argv[0]);
            std::exit(0);
        }
        else
        {
            std::cerr << "Unknown argument: " << a << "\n";
            return false;
        }
    }
    if (const char *env = std::getenv("SIREN_STOP_AT_FIRST"); env && std::string(env) == "1")
        cfg.stop_at_first = true;
    return true;
}

std::vector<AppSpec> loadPlannerReport(const std::string &path, double &tUpMs)
{
    std::ifstream f(path);
    if (!f)
        throw std::runtime_error("Cannot open input file: " + path);
    json j;
    f >> j;

    // t_up holds neither a HARQ process nor RBs but counts towards every
    // packet's deadline, so the windows below depend on it. No default: a
    // report without it (one written before C stopped including t_up) would
    // otherwise be scheduled as if packets had no upper-layer delay.
    if (!j.contains("parameters") || !j["parameters"].contains("t_up_ms") ||
        !j["parameters"]["t_up_ms"].is_number())
        throw std::runtime_error(
            "Input has no parameters.t_up_ms (the upper-layer delay); regenerate it with "
            "the current planner: " +
            path);
    tUpMs = j["parameters"]["t_up_ms"].get<double>();
    if (!(tUpMs >= 0.0))
        throw std::runtime_error("parameters.t_up_ms must be >= 0 in " + path);

    std::vector<AppSpec> apps;
    for (const auto &ja : j.at("applications"))
    {
        AppSpec a;
        a.app = ja.at("app").get<std::string>();
        a.deadline = ja.at("deadline").get<double>();
        a.error = ja.value("error", 0.0);
        a.packet_size = ja.value("packet_size", 0);
        a.period = ja.at("period").get<double>();
        a.type = ja.value("type", "");
        a.feasible = ja.value("feasible", true);
        for (const auto &jso : ja.at("segmentation_options"))
        {
            SegOption so;
            so.num_segments = jso.at("num_segments").get<int>();
            so.payload_per_segment = jso.value("payload_per_segment", 0);
            so.tb_error = jso.value("tb_error", 0.0);
            so.tbs = jso.value("tbs", 0);
            for (const auto &jm : jso.at("mbc_combinations"))
            {
                MbcCombo mc;
                mc.b = jm.at("b").get<int>();
                mc.c = jm.at("c").get<double>();
                mc.m = jm.at("m").get<int>();
                mc.a = jm.value("a", 0);
                so.mbc_combinations.push_back(mc);
            }
            a.segmentation_options.push_back(so);
        }
        apps.push_back(a);
    }
    return apps;
}

long long periodToTicks(double ms, double tti_ms, const std::string &what)
{
    const double ticks = ms / tti_ms;
    const long long rounded = std::llround(ticks);
    if (std::fabs(ticks - static_cast<double>(rounded)) > 1e-6 || rounded <= 0)
    {
        throw std::runtime_error(
            "Period of " + what + " (" + std::to_string(ms) + " ms) is not a whole "
                                                              "number of " +
            std::to_string(tti_ms) + " ms TTIs; the hyperperiod would "
                                     "not be a feasibility interval. Adjust the period or --tti.");
    }
    return rounded;
}

long long deadlineToTicks(double ms, double tti_ms)
{
    // Truncate: a deadline between two ticks is only guaranteed at the earlier one.
    return static_cast<long long>(std::floor(ms / tti_ms + 1e-9));
}

long long wcetToTicks(double ms, double tti_ms)
{
    // Round up: a TB occupies every TTI it touches, including a partial one.
    return static_cast<long long>(std::ceil(ms / tti_ms - 1e-9));
}

ChosenTask resolveChoice(const AppSpec &app, const AppChoice &c, double tti_ms, double t_up_ms)
{
    const SegOption &so = app.segmentation_options[c.segIdx];
    const MbcCombo &mc = so.mbc_combinations[c.mbcIdx];
    ChosenTask t;
    t.app = app.app;
    t.period = periodToTicks(app.period, tti_ms, app.app);
    t.deadline = deadlineToTicks(app.deadline, tti_ms);
    t.c = wcetToTicks(mc.c, tti_ms);
    // Rounded up, like C: a longer t_up only shortens the window a TB has.
    t.t_up = wcetToTicks(t_up_ms, tti_ms);
    t.b = mc.b;
    t.num_segments = so.num_segments;
    t.m = mc.m;
    t.tbs = so.tbs;
    t.attempts = mc.a;
    t.period_ms = app.period;
    t.deadline_ms = app.deadline;
    t.c_ms = mc.c;
    return t;
}

long long gcdll(long long a, long long b)
{
    return b == 0 ? a : gcdll(b, a % b);
}

long long lcmll(long long a, long long b)
{
    return a / gcdll(a, b) * b;
}

namespace
{

    GRBenv *solverEnv = nullptr;

    // Frees a model on every return path of solveCandidate.
    struct ModelGuard
    {
        GRBmodel *model;
        ~ModelGuard()
        {
            if (model)
                GRBfreemodel(model);
        }
    };

    // Gurobi's optimization status codes (Status attribute), as text; the C API
    // has no function for this.
    std::string gurobiStatusText(int status)
    {
        switch (status)
        {
        case GRB_LOADED:
            return "LOADED";
        case GRB_OPTIMAL:
            return "OPTIMAL";
        case GRB_INFEASIBLE:
            return "INFEASIBLE";
        case GRB_INF_OR_UNBD:
            return "INF_OR_UNBD";
        case GRB_UNBOUNDED:
            return "UNBOUNDED";
        case GRB_CUTOFF:
            return "CUTOFF";
        case GRB_ITERATION_LIMIT:
            return "ITERATION_LIMIT";
        case GRB_NODE_LIMIT:
            return "NODE_LIMIT";
        case GRB_TIME_LIMIT:
            return "TIME_LIMIT";
        case GRB_SOLUTION_LIMIT:
            return "SOLUTION_LIMIT";
        case GRB_INTERRUPTED:
            return "INTERRUPTED";
        case GRB_NUMERIC:
            return "NUMERIC";
        case GRB_SUBOPTIMAL:
            return "SUBOPTIMAL";
        case GRB_INPROGRESS:
            return "INPROGRESS";
        case GRB_USER_OBJ_LIMIT:
            return "USER_OBJ_LIMIT";
        case GRB_WORK_LIMIT:
            return "WORK_LIMIT";
        case GRB_MEM_LIMIT:
            return "MEM_LIMIT";
        default:
            return "status " + std::to_string(status);
        }
    }

} // namespace

bool startSolver(std::string &error)
{
    if (solverEnv)
        return true;
    GRBenv *env = nullptr;
    int err = GRBemptyenv(&env);
    // Set before GRBstartenv, so that every model inherits them.
    //   OutputFlag 0: no log on stdout, where the report goes without
    //     --output-scheduling.
    //   Threads 1: Gurobi otherwise uses several threads, and the
    //     experiments run one process per core (run_paper_experiment.sh);
    //     solver threads that starve each other can overrun a wall-clock cap.
    //   MIPGap = MIPGapAbs = 0: every objective here is a small integer (a
    //     count of processes, or a resource-block peak), so close the gap
    //     exactly rather than stopping at the default 1e-4 relative gap and
    //     reporting an off-by-one optimum.
    if (!err)
        err = GRBsetintparam(env, GRB_INT_PAR_OUTPUTFLAG, 0);
    if (!err)
        err = GRBsetintparam(env, GRB_INT_PAR_THREADS, 1);
    if (!err)
        err = GRBsetdblparam(env, GRB_DBL_PAR_MIPGAP, 0.0);
    if (!err)
        err = GRBsetdblparam(env, GRB_DBL_PAR_MIPGAPABS, 0.0);
    // Checks out the license (GRB_LICENSE_FILE, or gurobi.lic in a default
    // location).
    if (!err)
        err = GRBstartenv(env);
    if (err)
    {
        error = "Gurobi error " + std::to_string(err) + (env ? std::string(": ") + GRBgeterrormsg(env) : std::string());
        if (env)
            GRBfreeenv(env);
        return false;
    }
    solverEnv = env;
    return true;
}

std::string solverName()
{
    int major = 0, minor = 0, technical = 0;
    GRBversion(&major, &minor, &technical);
    return "gurobi " + std::to_string(major) + "." + std::to_string(minor) + "." + std::to_string(technical);
}

void stopSolver()
{
    if (solverEnv)
        GRBfreeenv(solverEnv);
    solverEnv = nullptr;
}

SolveResult solveCandidate(const std::vector<ChosenTask> &tasks,
                           int numHARQ, int Bmax,
                           const std::string &objective,
                           double remainingBudgetSeconds)
{
    SolveResult res;

    // Hyperperiod, in TTIs
    long long H = tasks.front().period;
    for (const auto &t : tasks)
        H = lcmll(H, t.period);
    res.hyperperiod = H;

    // The hyperperiod is only a feasibility interval under constrained
    // deadlines (Section VI). With D > T a job's window would also run past
    // the end of the capacity arrays below, so reject it here rather than
    // indexing out of bounds.
    for (const auto &t : tasks)
    {
        if (t.deadline > t.period)
        {
            res.constrained_deadline_violation = true;
            res.schedulable = false;
            return res;
        }
    }

    // Expand jobs
    std::vector<Job> jobs;
    for (size_t ai = 0; ai < tasks.size(); ++ai)
    {
        const auto &t = tasks[ai];
        long long reps = H / t.period;
        for (long long r = 0; r < reps; ++r)
        {
            long long release = r * t.period;
            long long dAbs = release + t.deadline;
            int groupId = static_cast<int>(ai) * 1000000 + static_cast<int>(r);
            for (int a = 0; a < t.num_segments; ++a)
            {
                Job j;
                j.appIdx = static_cast<int>(ai);
                j.release = release;
                j.deadlineAbs = dAbs;
                // The packet is delivered t_up after its last TB ends, so a
                // TB has to end t_up before the packet's deadline.
                j.latestEnd = dAbs - t.t_up;
                j.C = t.c;
                j.B = t.b;
                j.releaseGroup = groupId;
                j.subtaskIdx = a;
                jobs.push_back(j);
            }
        }
    }
    res.num_jobs = static_cast<long long>(jobs.size());

    // Trivial infeasibility check: every job needs a non-empty start window
    // [r, d - t_up - C]
    for (const auto &j : jobs)
    {
        if (j.latestEnd - j.C < j.release)
        {
            res.trivially_infeasible = true;
            res.schedulable = false;
            return res;
        }
    }

    // --- Build the ILP ---
    ModelBuilder build;
    int nextCol = 0;

    std::vector<std::vector<int>> jobCols(jobs.size());           // job -> list of its column indices
    std::vector<std::vector<long long>> jobColStart(jobs.size()); // job -> start time for each column (aligned with jobCols)
    std::vector<int> colMachine;                                  // col -> machine index (x-columns only)

    // Per-machine list of that machine's x-columns (for the "machines" objective constraint)
    std::vector<std::vector<int>> machineAllCols(numHARQ);

    // Accumulators for time-indexed capacity constraints:
    // machineCap[i][t] = list of columns occupying machine i at time t
    // resourceCap[t]   = list of (col, coeff=B_j)
    std::vector<std::vector<std::vector<int>>> machineCapCols(numHARQ, std::vector<std::vector<int>>(H));
    std::vector<std::vector<std::pair<int, double>>> resourceCapTerms(H);

    for (size_t ji = 0; ji < jobs.size(); ++ji)
    {
        const Job &j = jobs[ji];
        long long hMin = j.release;
        long long hMax = j.latestEnd - j.C; // inclusive: ends t_up before the deadline
        for (int i = 0; i < numHARQ; ++i)
        {
            for (long long h = hMin; h <= hMax; ++h)
            {
                int col = nextCol++;
                build.setColumnBounds(col, 0.0, 1.0);
                build.setInteger(col);
                build.setObjective(col, 0.0); // default; overwritten later for "machines" objective
                jobCols[ji].push_back(col);
                jobColStart[ji].push_back(h);
                colMachine.push_back(i);
                machineAllCols[i].push_back(col);
                for (long long tt = h; tt < h + j.C; ++tt)
                {
                    machineCapCols[i][tt].push_back(col);
                    resourceCapTerms[tt].push_back({col, static_cast<double>(j.B)});
                }
            }
        }
    }

    // Auxiliary objective variables.
    //
    // These are allocated here, ahead of the constraint rows, because the
    // resource-capacity rows below have to reference the peak variable P by
    // column index while they are being built.
    //
    //   rb_peak : one integer P = B^peak in [0, Bmax], minimize P   (section 5.3)
    //   harq    : one binary y_i = u_p per process, minimize sum y_i (section 5.2)
    int peakCol = -1;
    std::vector<int> yCols; // machine -> its y column (only set when objective == "harq")
    if (objective == "rb_peak")
    {
        peakCol = nextCol++;
        // P is integer, so every column is and the model is a pure ILP. (Every
        // B_j is integral and every x binary, so P would land on an integer at
        // the optimum even if it were continuous.)
        build.setColumnBounds(peakCol, 0.0, static_cast<double>(Bmax));
        build.setInteger(peakCol);
        build.setObjective(peakCol, 1.0);
    }
    else if (objective == "harq")
    {
        for (int i = 0; i < numHARQ; ++i)
        {
            int yi = nextCol++;
            build.setColumnBounds(yi, 0.0, 1.0);
            build.setInteger(yi);
            build.setObjective(yi, 1.0);
            yCols.push_back(yi);
        }
    }
    res.num_variables = nextCol;
    long long Qjobs = static_cast<long long>(jobs.size());

    long long numRows = 0;

    // Assignment constraints: each job starts exactly once
    for (size_t ji = 0; ji < jobs.size(); ++ji)
    {
        std::vector<int> cols = jobCols[ji];
        std::vector<double> vals(cols.size(), 1.0);
        build.addRow(static_cast<int>(cols.size()), cols.data(), vals.data(), GRB_EQUAL, 1.0);
        numRows++;
    }

    // Machine capacity constraints
    for (int i = 0; i < numHARQ; ++i)
    {
        for (long long t = 0; t < H; ++t)
        {
            if (machineCapCols[i][t].empty())
                continue;
            std::vector<double> vals(machineCapCols[i][t].size(), 1.0);
            build.addRow(static_cast<int>(machineCapCols[i][t].size()),
                         machineCapCols[i][t].data(), vals.data(), GRB_LESS_EQUAL, 1.0);
            numRows++;
        }
    }

    // Resource capacity constraints
    //
    // Default form                 : sum_j B_j * x_j,h <= Bmax
    // Under the rb_peak objective  : sum_j B_j * x_j,h -  P  <= 0, with P <= Bmax
    //
    // The second form still enforces the hard Bmax cap (through P's upper
    // bound) while making P an upper bound on usage at every time step, so
    // minimizing P minimizes the peak.
    for (long long t = 0; t < H; ++t)
    {
        if (resourceCapTerms[t].empty())
            continue;
        std::vector<int> cols;
        std::vector<double> vals;
        cols.reserve(resourceCapTerms[t].size() + 1);
        vals.reserve(resourceCapTerms[t].size() + 1);
        for (auto &pr : resourceCapTerms[t])
        {
            cols.push_back(pr.first);
            vals.push_back(pr.second);
        }
        if (peakCol >= 0)
        {
            cols.push_back(peakCol);
            vals.push_back(-1.0);
            build.addRow(static_cast<int>(cols.size()), cols.data(), vals.data(), GRB_LESS_EQUAL, 0.0);
        }
        else
        {
            build.addRow(static_cast<int>(cols.size()), cols.data(), vals.data(), GRB_LESS_EQUAL, static_cast<double>(Bmax));
        }
        numRows++;
    }

    // Symmetry-breaking constraints: within the same (app, release) group,
    // subtask a must start no later than subtask a+1.
    {
        // group jobs by releaseGroup, ordered by subtaskIdx (already generated in order)
        std::vector<size_t> order(jobs.size());
        std::iota(order.begin(), order.end(), 0);
        std::stable_sort(order.begin(), order.end(), [&](size_t x, size_t y)
                         {
            if (jobs[x].releaseGroup != jobs[y].releaseGroup) return jobs[x].releaseGroup < jobs[y].releaseGroup;
            return jobs[x].subtaskIdx < jobs[y].subtaskIdx; });
        for (size_t idx = 0; idx + 1 < order.size(); ++idx)
        {
            size_t ja = order[idx];
            size_t jb = order[idx + 1];
            if (jobs[ja].releaseGroup != jobs[jb].releaseGroup)
                continue;
            // S_ja - S_jb <= 0 ,  S_j = sum_col start(col) * x_col
            std::vector<int> cols;
            std::vector<double> vals;
            cols.reserve(jobCols[ja].size() + jobCols[jb].size());
            vals.reserve(jobCols[ja].size() + jobCols[jb].size());
            for (size_t c = 0; c < jobCols[ja].size(); ++c)
            {
                cols.push_back(jobCols[ja][c]);
                vals.push_back(static_cast<double>(jobColStart[ja][c]));
            }
            for (size_t c = 0; c < jobCols[jb].size(); ++c)
            {
                cols.push_back(jobCols[jb][c]);
                vals.push_back(-static_cast<double>(jobColStart[jb][c]));
            }
            build.addRow(static_cast<int>(cols.size()), cols.data(), vals.data(), GRB_LESS_EQUAL, 0.0);
            numRows++;
        }
    }

    // Objective: minimize HARQ processes used (section 5.2)
    // y_i in {0,1} (allocated above) with sum_j sum_h x_i,j,h <= Q * y_i,
    // minimize sum_i y_i
    if (objective == "harq")
    {
        for (int i = 0; i < numHARQ; ++i)
        {
            int yi = yCols[i];

            std::vector<int> cols = machineAllCols[i];
            cols.push_back(yi);
            std::vector<double> vals(machineAllCols[i].size(), 1.0);
            vals.push_back(-static_cast<double>(Qjobs));
            // sum x_i,j,h - Q*y_i <= 0
            build.addRow(static_cast<int>(cols.size()), cols.data(), vals.data(), GRB_LESS_EQUAL, 0.0);
            numRows++;
        }
    }
    res.num_constraints = numRows;

    GRBenv *env = solverEnv;
    if (!env)
    {
        res.error = true;
        res.status = "no Gurobi environment (startSolver was not called)";
        return res;
    }

    // Only two outcomes decide a candidate: a table (GRB_OPTIMAL), or a proof
    // that none exists (below). Anything else leaves it undecided -- a
    // timeout, or an error -- and an undecided candidate never makes a flow
    // set "proved unschedulable".
    GRBmodel *model = nullptr;
    if (int err = build.loadInto(env, &model))
    {
        res.error = true;
        res.status = "model rejected by Gurobi (" + std::to_string(err) + ": " + GRBgeterrormsg(env) + ")";
        return res;
    }
    ModelGuard guard{model};

    // The model has its own copy of the environment's parameters (one
    // thread, no output, zero gaps: see startSolver); only the time limit is
    // per candidate.
    GRBenv *menv = GRBgetenv(model);
    double budget = std::max(1.0, remainingBudgetSeconds);
    if (int err = GRBsetdblparam(menv, GRB_DBL_PAR_TIMELIMIT, budget))
    {
        res.error = true;
        res.status = "cannot set TimeLimit (" + std::to_string(err) + ": " + GRBgeterrormsg(menv) + ")";
        return res;
    }

    auto t0 = Clock::now();
    int runErr = GRBoptimize(model);
    auto t1 = Clock::now();
    res.solve_time_ms = std::chrono::duration<double, std::milli>(t1 - t0).count();

    if (runErr)
    {
        res.error = true;
        res.status = "GRBoptimize returned an error (" + std::to_string(runErr) + ": " + GRBgeterrormsg(menv) + ")";
        return res;
    }

    int status = 0;
    if (int err = GRBgetintattr(model, GRB_INT_ATTR_STATUS, &status))
    {
        res.error = true;
        res.status = "cannot read Status (" + std::to_string(err) + ": " + GRBgeterrormsg(menv) + ")";
        return res;
    }
    res.status = gurobiStatusText(status);

    // Hitting the budget without a proof either way
    // counts as a timeout, even if an incumbent was found. (If you would
    // rather bank an unproven-but-feasible incumbent, test the SolCount
    // attribute > 0 here.)
    if (status == GRB_TIME_LIMIT || status == GRB_ITERATION_LIMIT)
    {
        res.timed_out = true;
        res.schedulable = false;
        return res;
    }

    // A proof of infeasibility. GRB_INF_OR_UNBD is one too: every column of
    // this model is bounded (binaries, and the integer P in [0, B^max]), so
    // the model cannot be unbounded.
    if (status == GRB_INFEASIBLE || status == GRB_INF_OR_UNBD)
    {
        res.schedulable = false;
        return res;
    }

    if (status != GRB_OPTIMAL)
    {
        res.error = true;
        res.schedulable = false;
        return res;
    }

    std::vector<double> solution(static_cast<size_t>(build.numColumns()));
    if (int err = GRBgetdblattrarray(model, GRB_DBL_ATTR_X, 0, build.numColumns(), solution.data()))
    {
        res.error = true;
        res.schedulable = false;
        res.status = "optimal, but cannot read X (" + std::to_string(err) + ": " + GRBgeterrormsg(menv) + ")";
        return res;
    }

    {
        res.schedulable = true;
        const double *sol = solution.data();

        // Extract the dispatch table: for each job, find its chosen (machine, start) column.
        res.schedule.reserve(jobs.size());
        for (size_t ji = 0; ji < jobs.size(); ++ji)
        {
            const Job &j = jobs[ji];
            int chosenCol = -1;
            for (int col : jobCols[ji])
            {
                if (sol[col] > 0.5)
                {
                    chosenCol = col;
                    break;
                }
            }
            if (chosenCol < 0)
            {
                // Cannot happen for a solution satisfying the assignment
                // rows; if it does, the table would silently lack a job, so
                // the candidate is left undecided instead.
                res.schedulable = false;
                res.error = true;
                res.status = "optimal solution places no start for a job";
                res.schedule.clear();
                return res;
            }
            // find matching start time (aligned index in jobColStart)
            long long start = -1;
            for (size_t c = 0; c < jobCols[ji].size(); ++c)
            {
                if (jobCols[ji][c] == chosenCol)
                {
                    start = jobColStart[ji][c];
                    break;
                }
            }
            ScheduleEntry se;
            se.app = tasks[j.appIdx].app;
            se.subtask = j.subtaskIdx;
            se.machine = colMachine[chosenCol];
            se.release = j.release;
            se.start = start;
            se.end = start + j.C;
            se.deadline = j.deadlineAbs;
            se.b = j.B;
            res.schedule.push_back(se);
        }
        std::sort(res.schedule.begin(), res.schedule.end(),
                  [](const ScheduleEntry &x, const ScheduleEntry &y)
                  {
                      if (x.start != y.start)
                          return x.start < y.start;
                      return x.machine < y.machine;
                  });

        if (objective == "harq" && !yCols.empty())
        {
            int used = 0;
            for (int yi : yCols)
                if (sol[yi] > 0.5)
                    used++;
            res.harq_used = used;
        }
        else if (objective == "rb_peak" && peakCol >= 0)
        {
            res.rb_peak_used = static_cast<int>(std::llround(sol[peakCol]));
        }
    }
    return res;
}