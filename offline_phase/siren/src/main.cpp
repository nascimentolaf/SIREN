// main.cpp
//
// Time-indexed ILP formulation for periodic parallel-machine scheduling
// with a global resource-block constraint, as specified in:
//   "An Optimized Time-Indexed ILP Formulation for Periodic Parallel
//    Machine Scheduling with Resource Constraints"
//
// Input : planner_report.json  (applications, segmentation_options, mbc_combinations)
// Output: <prefix>.json        (schedulable candidate task sets, offline_phase_report.json-style)
//
// Build (see CMakeLists.txt; GUROBI_HOME is the linux64 directory):
//   g++ -std=c++17 -O2 main.cpp scheduler.cpp -o siren \
//       -I$GUROBI_HOME/include -L$GUROBI_HOME/lib -lgurobi130
//

#include "scheduler.hpp"
#include <algorithm>
#include <cmath>
#include <limits>

namespace
{

    // Mean RB usage of a candidate tuple over its hyperperiod under the
    // conservative reservation (B_i RBs for the whole C_i of every job):
    // sum_i N_i * (H/T_i) * B_i * C_i / H = sum_i N_i B_i C_i / T_i. The peak of
    // any schedule is at least this, which is what bounds the rb_peak search.
    double meanRbUsage(const std::vector<ChosenTask> &tasks)
    {
        double mean = 0.0;
        for (const auto &t : tasks)
            mean += static_cast<double>(t.num_segments) * static_cast<double>(t.b) *
                    static_cast<double>(t.c) / static_cast<double>(t.period);
        return mean;
    }

    json flowsJson(const std::vector<ChosenTask> &tasks)
    {
        json flows = json::array();
        for (auto &t : tasks)
        {
            flows.push_back({{"app", t.app},
                             {"period_tti", t.period},
                             {"deadline_tti", t.deadline},
                             {"c_tti", t.c},
                             {"period", t.period_ms},
                             {"deadline", t.deadline_ms},
                             {"c", t.c_ms},
                             {"num_segments", t.num_segments},
                             {"a", t.attempts},
                             {"m", t.m},
                             {"b", t.b},
                             {"tbs", t.tbs}});
        }
        return flows;
    }

    void writeSchedule(const std::string &path, long long comboIndex, const json &flows,
                       const SolveResult &r, const Config &cfg, const std::string &objective)
    {
        json dispatch = json::array();
        for (auto &se : r.schedule)
        {
            dispatch.push_back({{"app", se.app},
                                {"subtask", se.subtask},
                                {"machine", se.machine},
                                {"release_tti", se.release},
                                {"start_tti", se.start},
                                {"end_tti", se.end},
                                {"deadline_tti", se.deadline},
                                {"release_ms", se.release * cfg.tti_ms},
                                {"start_ms", se.start * cfg.tti_ms},
                                {"end_ms", se.end * cfg.tti_ms},
                                {"deadline_ms", se.deadline * cfg.tti_ms},
                                {"b", se.b}});
        }
        // t_up_tti is what a reader adds to a packet's last end_tti to get its
        // delivery time, the one compared with deadline_tti (end_tti itself is
        // when the TB releases its HARQ process and RBs).
        json scheduleDoc = {
            {"combo_index", comboIndex},
            {"flows", flows},
            {"config", {{"n_rb", cfg.n_rb}, {"m_harq", cfg.m_harq}, {"objective", objective}, {"tti_ms", cfg.tti_ms}, {"t_up_ms", cfg.t_up_ms}, {"t_up_tti", wcetToTicks(cfg.t_up_ms, cfg.tti_ms)}}},
            {"hyperperiod_tti", r.hyperperiod},
            {"hyperperiod_ms", r.hyperperiod * cfg.tti_ms},
            {"harq_used", r.harq_used >= 0 ? json(r.harq_used) : json(nullptr)},
            {"rb_peak_used", r.rb_peak_used >= 0 ? json(r.rb_peak_used) : json(nullptr)},
            {"dispatch_table", dispatch}};
        std::ofstream sf(path);
        sf << scheduleDoc.dump(2);
    }

    // A candidate proved schedulable in pass 1, kept for the bounded pass 2.
    struct FoundCandidate
    {
        long long index = 0; // 1-based odometer position (combo_index)
        std::vector<ChosenTask> tasks;
        double mean_rb = 0.0;
        size_t entry = 0; // position in the "schedulable" array
        std::string schedule_file;
    };

} // namespace

int main(int argc, char **argv)
{
    Config cfg;
    if (!parseArgs(argc, argv, cfg))
        return 1;

    std::vector<AppSpec> apps;
    try
    {
        apps = loadPlannerReport(cfg.input_path, cfg.t_up_ms);
    }
    catch (const std::exception &e)
    {
        std::cerr << "Failed to load input: " << e.what() << "\n";
        return 1;
    }

    if (apps.empty())
    {
        std::cerr << "No applications found in input.\n";
        return 1;
    }

    // Flatten choices per app
    std::vector<std::vector<AppChoice>> perAppChoices(apps.size());
    for (size_t ai = 0; ai < apps.size(); ++ai)
    {
        for (size_t si = 0; si < apps[ai].segmentation_options.size(); ++si)
        {
            for (size_t mi = 0; mi < apps[ai].segmentation_options[si].mbc_combinations.size(); ++mi)
            {
                perAppChoices[ai].push_back({static_cast<int>(si), static_cast<int>(mi)});
            }
        }
    }

    // Eq. (6): a flow whose Pareto set is empty makes the whole flow set
    // infeasible. That is a schedulability verdict, not a tool failure -- a
    // sweep has to be able to count this flow set as unschedulable rather than
    // lose it to a non-zero exit status. (It also has to be caught before the
    // odometer below indexes an empty perAppChoices[ai] out of bounds.)
    json infeasibleFlows = json::array();
    for (size_t ai = 0; ai < apps.size(); ++ai)
    {
        if (perAppChoices[ai].empty())
            infeasibleFlows.push_back(apps[ai].app);
    }

    if (!infeasibleFlows.empty())
    {
        json out = {
            {"config", {{"solver", solverName()}, {"n_rb", cfg.n_rb}, {"m_harq", cfg.m_harq}, {"objective", cfg.objective}, {"tti_ms", cfg.tti_ms}, {"t_up_ms", cfg.t_up_ms}, {"timeout_s", cfg.timeout_seconds}, {"candidate_timeout_s", cfg.candidate_timeout_seconds}, {"stop_at_first", cfg.stop_at_first}}},
            {"num_combinations_total", 0},
            {"num_combinations_tested", 0},
            {"num_schedulable", 0},
            {"num_combinations_timed_out", 0},
            {"timed_out_combos", json::array()},
            {"num_combinations_errored", 0},
            {"errored_combos", json::array()},
            {"timed_out", false},
            {"infeasible_flows", infeasibleFlows},
            {"schedulable", json::array()}};

        if (cfg.have_output)
        {
            std::ofstream of(cfg.output_prefix);
            of << out.dump(2);
        }
        else
        {
            std::cout << out.dump(2) << std::endl;
        }

        std::cerr << "Flow set is infeasible: no transmission configuration exists for ";
        for (size_t i = 0; i < infeasibleFlows.size(); ++i)
            std::cerr << (i ? ", " : "") << infeasibleFlows[i].get<std::string>();
        std::cerr << " (empty Pareto set, Eq. (6)).\n";
        return 0;
    }

    // One Gurobi environment (one license checkout) for the whole sweep. A
    // run that cannot get a license fails here, as a tool failure: every
    // candidate would otherwise end as a solver error.
    std::string solverError;
    if (!startSolver(solverError))
    {
        std::cerr << "Cannot start Gurobi: " << solverError << "\n";
        return 1;
    }
    struct SolverStop
    {
        ~SolverStop() { stopSolver(); }
    } solverStop;

    long long totalCombos = 1;
    for (auto &v : perAppChoices)
        totalCombos *= static_cast<long long>(v.size());

    if (!cfg.quiet)
    {
        std::cerr << "Loaded " << apps.size() << " applications, "
                  << totalCombos << " candidate task-set combinations to test.\n";
        std::cerr << "Config: Bmax=" << cfg.n_rb
                  << " m_harq(machines)=" << cfg.m_harq << " t_up=" << cfg.t_up_ms << "ms"
                  << " objective=" << cfg.objective
                  << " timeout=" << cfg.timeout_seconds << "s" << (cfg.timeout_seconds > 0.0 ? "" : " (no sweep budget)");
        if (cfg.candidate_timeout_seconds > 0.0)
            std::cerr << " candidate-timeout=" << cfg.candidate_timeout_seconds << "s";
        std::cerr << "\n";
    }

    if (cfg.emit_schedules)
    {
        std::filesystem::create_directories(cfg.schedule_dir);
    }
    int comboDigits = std::max(1, static_cast<int>(std::to_string(totalCombos).size()));
    auto padIndex = [comboDigits](long long v)
    {
        std::string s = std::to_string(v);
        while (static_cast<int>(s.size()) < comboDigits)
            s = "0" + s;
        return s;
    };

    // Bounded rb_peak search: pass 1 runs the feasibility objective, pass 2
    // (below the loop) minimises the peak over what pass 1 proved schedulable.
    const bool bounded = cfg.objective == "rb_peak" && cfg.rb_peak_search == "bounded";
    const std::string passOneObjective = bounded ? "feasibility" : cfg.objective;
    std::vector<FoundCandidate> found;

    json outSchedulable = json::array();
    long long numTested = 0;
    long long numSchedulable = 0;
    long long numCandidatesTimedOut = 0;
    json timedOutCombos = json::array();
    // Candidates Gurobi neither decided nor timed out on (SolveResult::error):
    // undecided, like a timeout, but listed apart so they cannot pass unseen.
    long long numCandidatesErrored = 0;
    json erroredCombos = json::array();
    bool globalTimedOut = false;

    auto startTime = Clock::now();
    std::vector<int> idx(apps.size(), 0);

    while (true)
    {
        double elapsed = std::chrono::duration<double>(Clock::now() - startTime).count();
        // A zero --timeout means no sweep budget: every candidate is tested,
        // each under the per-candidate cap only (RQ3 residual-capacity sweep).
        double remaining = cfg.timeout_seconds > 0.0
                               ? cfg.timeout_seconds - elapsed
                               : std::numeric_limits<double>::infinity();
        if (remaining <= 0.0)
        {
            globalTimedOut = true;
            break;
        }

        // Build candidate task set. resolveChoice rejects a period that does
        // not land on the TTI grid; that is a property of the input, identical
        // for every candidate, so report it once and stop rather than let it
        // escape main uncaught.
        std::vector<ChosenTask> tasks;
        tasks.reserve(apps.size());
        try
        {
            for (size_t ai = 0; ai < apps.size(); ++ai)
            {
                tasks.push_back(resolveChoice(apps[ai], perAppChoices[ai][idx[ai]], cfg.tti_ms, cfg.t_up_ms));
            }
        }
        catch (const std::exception &e)
        {
            std::cerr << "Cannot place this task set on a " << cfg.tti_ms
                      << " ms grid: " << e.what() << "\n";
            return 1;
        }

        // The solver gets whichever cap bites first: what is left of the
        // flow set's global budget, or the per-candidate cap.
        double budget = remaining;
        bool candidateCapped = cfg.candidate_timeout_seconds > 0.0 &&
                               cfg.candidate_timeout_seconds < remaining;
        if (candidateCapped)
            budget = cfg.candidate_timeout_seconds;

        SolveResult r = solveCandidate(tasks, cfg.m_harq, cfg.n_rb, passOneObjective, budget);
        numTested++;

        if (!cfg.quiet)
        {
            std::cerr << "[" << numTested << "/" << totalCombos << "] ";
            for (auto &t : tasks)
                std::cerr << t.app << "(N=" << t.num_segments
                          << ",C=" << t.c << "tti,b=" << t.b << ") ";
            if (r.constrained_deadline_violation)
                std::cerr << "-> infeasible (D > T)\n";
            else if (r.trivially_infeasible)
                std::cerr << "-> infeasible (window)\n";
            else if (r.timed_out)
                std::cerr << (candidateCapped ? "-> timed out, skipped\n" : "-> timed out\n");
            else if (r.error)
                // Same "(<t> ms)" as the decided lines, so the log parsers
                // (plot_paper.py --derive, rq4_hyperperiod.py) can time it.
                std::cerr << "-> solver error (" << r.solve_time_ms << " ms) [" << r.status << "]\n";
            else
                std::cerr << "-> " << (r.schedulable ? "SCHEDULABLE" : "not schedulable")
                          << " (" << r.solve_time_ms << " ms)\n";
        }

        if (r.schedulable)
        {
            numSchedulable++;
            json flows = flowsJson(tasks);

            std::string scheduleFile;
            if (cfg.emit_schedules)
            {
                scheduleFile = cfg.schedule_dir + "/combo_" + padIndex(numTested) + ".json";
                writeSchedule(scheduleFile, numTested, flows, r, cfg, passOneObjective);
            }

            json entry = {
                {"flows", flows},
                {"schedulable", true},
                {"hyperperiod_tti", r.hyperperiod},
                {"hyperperiod_ms", r.hyperperiod * cfg.tti_ms},
                {"num_jobs", r.num_jobs},
                {"num_variables", r.num_variables},
                {"num_constraints", r.num_constraints},
                {"solve_time_ms", r.solve_time_ms}};
            if (r.harq_used >= 0)
                entry["harq_used"] = r.harq_used;
            if (r.rb_peak_used >= 0)
                entry["rb_peak_used"] = r.rb_peak_used;
            if (!scheduleFile.empty())
                entry["schedule_file"] = scheduleFile;
            if (bounded)
            {
                entry["mean_rb"] = meanRbUsage(tasks);
                found.push_back({numTested, tasks, meanRbUsage(tasks), outSchedulable.size(), scheduleFile});
            }
            outSchedulable.push_back(entry);
            if (cfg.stop_at_first)
                break; // verdict mode: one table decides the flow set
        }

        if (r.timed_out)
        {
            // Either way this candidate was tested but never decided, so it is
            // listed as such -- a reader must not count it as proved
            // unschedulable. The difference is what happens next: a candidate
            // that ran out its own cap is a skip, and the remaining candidates
            // of this flow set are still worth testing, while the global budget
            // running out ends the sweep.
            numCandidatesTimedOut++;
            timedOutCombos.push_back(numTested);
            if (!candidateCapped)
            {
                globalTimedOut = true;
                break;
            }
        }

        if (r.error)
        {
            // Undecided, so the flow set cannot be proved unschedulable; the
            // sweep goes on, as after a candidate that hit its own cap.
            numCandidatesErrored++;
            erroredCombos.push_back(numTested);
        }

        // odometer increment
        int p = static_cast<int>(apps.size()) - 1;
        while (p >= 0)
        {
            idx[p]++;
            if (idx[p] < static_cast<int>(perAppChoices[p].size()))
                break;
            idx[p] = 0;
            p--;
        }
        if (p < 0)
            break; // enumerated all combinations
    }

    // ------------------------------------------------------------------
    // Pass 2 of the bounded rb_peak search. The schedulable candidates are
    // taken in ascending mean-RB order; each is re-solved with the rb_peak
    // objective and the best peak is kept. Since a schedule's peak is never
    // below its mean usage, once the best peak is <= ceil(mean) of the next
    // candidate no remaining candidate can improve it and the search stops.
    // A candidate that hits the per-candidate cap is skipped and counted, so
    // the best peak is optimal over the decided candidates only when that
    // count is zero.
    // ------------------------------------------------------------------
    json rbPeakSearch;
    if (bounded)
    {
        std::stable_sort(found.begin(), found.end(),
                         [](const FoundCandidate &a, const FoundCandidate &b)
                         { return a.mean_rb < b.mean_rb; });
        long long solved = 0, pruned = 0, timedOut = 0, errored = 0;
        int bestPeak = -1;
        long long bestIndex = 0;
        bool budgetOut = globalTimedOut;
        for (size_t k = 0; k < found.size(); ++k)
        {
            const auto &f = found[k];
            if (bestPeak >= 0 && static_cast<double>(bestPeak) <= std::ceil(f.mean_rb - 1e-9))
            {
                pruned = static_cast<long long>(found.size() - k);
                break;
            }
            double elapsed = std::chrono::duration<double>(Clock::now() - startTime).count();
            double remaining = cfg.timeout_seconds > 0.0
                                   ? cfg.timeout_seconds - elapsed
                                   : std::numeric_limits<double>::infinity();
            if (remaining <= 0.0)
            {
                budgetOut = true;
                pruned = static_cast<long long>(found.size() - k);
                break;
            }
            double budget = remaining;
            if (cfg.candidate_timeout_seconds > 0.0 && cfg.candidate_timeout_seconds < remaining)
                budget = cfg.candidate_timeout_seconds;

            SolveResult r = solveCandidate(f.tasks, cfg.m_harq, cfg.n_rb, "rb_peak", budget);
            json &entry = outSchedulable[f.entry];
            if (!cfg.quiet)
                std::cerr << "[rb_peak " << (k + 1) << "/" << found.size() << "] combo " << f.index
                          << " mean=" << f.mean_rb << " -> ";
            if (r.schedulable && r.rb_peak_used >= 0)
            {
                solved++;
                entry["rb_peak_used"] = r.rb_peak_used;
                entry["rb_peak_solve_time_ms"] = r.solve_time_ms;
                if (!f.schedule_file.empty())
                    writeSchedule(f.schedule_file, f.index, entry["flows"], r, cfg, "rb_peak");
                if (bestPeak < 0 || r.rb_peak_used < bestPeak)
                {
                    bestPeak = r.rb_peak_used;
                    bestIndex = f.index;
                }
                if (!cfg.quiet)
                    std::cerr << "peak " << r.rb_peak_used << " (" << r.solve_time_ms << " ms)\n";
            }
            else if (r.timed_out)
            {
                timedOut++;
                entry["rb_peak_timed_out"] = true;
                if (!cfg.quiet)
                    std::cerr << "timed out, skipped\n";
            }
            else
            {
                // Pass 1 found a table, so the peak model is feasible: any
                // other outcome is a solver error. The candidate keeps its
                // pass-1 table; its peak is unknown.
                errored++;
                entry["rb_peak_error"] = r.status.empty() ? std::string("no table") : r.status;
                if (!cfg.quiet)
                    std::cerr << "solver error, skipped (" << r.status << ")\n";
            }
        }
        rbPeakSearch = {{"mode", "bounded"},
                        {"num_candidates", static_cast<long long>(found.size())},
                        {"num_solved", solved},
                        {"num_pruned", pruned},
                        {"num_timed_out", timedOut},
                        {"num_errors", errored},
                        {"budget_exhausted", budgetOut},
                        {"best_rb_peak", bestPeak >= 0 ? json(bestPeak) : json(nullptr)},
                        {"best_combo_index", bestPeak >= 0 ? json(bestIndex) : json(nullptr)},
                        {"optimal_over_decided", bestPeak >= 0 && timedOut == 0 && errored == 0 && !budgetOut}};
        if (!cfg.quiet)
            std::cerr << "rb_peak search: " << solved << " solved, " << pruned << " pruned by the bound, "
                      << timedOut << " timed out, " << errored << " solver error(s); best peak "
                      << (bestPeak >= 0 ? std::to_string(bestPeak) : std::string("none")) << "\n";
    }

    json out = {
        {"config", {{"solver", solverName()}, {"n_rb", cfg.n_rb}, {"m_harq", cfg.m_harq}, {"objective", cfg.objective}, {"rb_peak_search", cfg.rb_peak_search}, {"tti_ms", cfg.tti_ms}, {"t_up_ms", cfg.t_up_ms}, {"timeout_s", cfg.timeout_seconds}, {"candidate_timeout_s", cfg.candidate_timeout_seconds}, {"stop_at_first", cfg.stop_at_first}}},
        {"num_combinations_total", totalCombos},
        {"num_combinations_tested", numTested},
        {"num_schedulable", numSchedulable},
        {"num_combinations_timed_out", numCandidatesTimedOut},
        {"timed_out_combos", timedOutCombos},
        {"num_combinations_errored", numCandidatesErrored},
        {"errored_combos", erroredCombos},
        {"timed_out", globalTimedOut},
        {"infeasible_flows", json::array()},
        {"schedulable", outSchedulable}};
    if (bounded)
        out["rb_peak_search"] = rbPeakSearch;

    if (cfg.have_output)
    {
        std::string outPath = cfg.output_prefix;
        std::ofstream of(outPath);
        of << out.dump(2);
        if (!cfg.quiet)
            std::cerr << "Wrote results to " << outPath << "\n";
    }
    else
    {
        std::cout << out.dump(2) << std::endl;
    }

    if (!cfg.quiet)
    {
        std::cerr << "Tested " << numTested << "/" << totalCombos
                  << " combinations, found " << numSchedulable << " schedulable"
                  << (globalTimedOut ? " (timed out before finishing)" : "")
                  << (cfg.stop_at_first && numSchedulable ? " (stopped at the first: verdict mode)" : "") << ".\n";
        if (numCandidatesErrored > 0)
            std::cerr << "WARNING: " << numCandidatesErrored
                      << " candidate(s) ended in a solver error; they are undecided.\n";
    }

    return 0;
}