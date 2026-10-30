#include "qospf.hpp"

int main(int argc, char **argv)
{
    if (argc < 3)
    {
        std::cerr << "Usage: " << argv[0] << " <flowset.json> <mcs_configurations.json> [output.json]\n";
        return 1;
    }

    try
    {
        // --- Environment (all required; no silent defaults) ---
        int RBs = require_env_int("RBs");
        int HARQs = require_env_int("HARQs");

        // Radio and Section V parameters, read exactly as rr/pf/5gtps read
        // them, so no method can end up with a different A^max, RLC header or
        // RB budget than the others.
        const RuntimeConfig runtime = read_runtime_config();
        double alpha = require_env_double("ALPHA");
        double beta = require_env_double("BETA");
        double gamma = require_env_double("GAMMA");
        double lambda = require_env_double("PF_EMA_LAMBDA");

        // --- Inputs ---
        std::ifstream flowset_file(argv[1]);
        if (!flowset_file)
            throw std::runtime_error("Cannot open flowset file: " + std::string(argv[1]));
        json flowset_json;
        flowset_file >> flowset_json;

        std::vector<FlowSpec> flowspecs = parse_flowset(flowset_json);
        // Only the tables at SINR_DB, as every other method reads them.
        std::vector<urllc::TbsTable> mcs_table = parse_mcs_table(argv[2], runtime.sinr_db);
        qos_data qd;

        std::vector<ResolvedFlow> flows;
        flows.reserve(flowspecs.size());
        for (const auto &fs : flowspecs)
        {
            ResolvedFlow rf = resolve_flow(fs, mcs_table, runtime);
            assign_5qi(rf, qd);
            flows.push_back(rf);
        }
        normalize_priorities(flows);

        std::map<std::string, int> app_to_idx;
        for (size_t i = 0; i < flows.size(); ++i)
            app_to_idx[flows[i].app] = static_cast<int>(i);

        long long hyperperiod_ms = compute_hyperperiod(flows);

        // The QoS context behind the P and G terms of U_i(t). The 5QI is
        // taken from the app database when it declares one and inferred from
        // (deadline, error) otherwise; GBR has no counterpart in the flowset
        // at all and is always derived as payload/period. Both are reported
        // because they are the part of the mapping most worth checking
        // against intent.
        std::cerr << "QoS context:\n";
        for (size_t i = 0; i < flows.size(); ++i)
        {
            const ResolvedFlow &f = flows[i];
            std::cerr << "  " << f.app
                      << "  deadline=" << f.deadline << "ms"
                      << "  error=" << f.error
                      << "  -> 5QI=" << f.fiveQI
                      << (flowspecs[i].fiveQI >= 0 ? " (declared)" : " (inferred)")
                      << "  priority_level=" << f.priority_level
                      << "  P=" << f.priority_scalar
                      << "  GBR=" << f.required_rate_bps << " bits/ms\n";
        }

        // Per-flow running throughput EMA (R_bar). Initialized to a small
        // epsilon so unserved flows start with very high urgency (M_i),
        // matching the PF intent of boosting under-served flows.
        const double EPS = 1e-6;
        std::vector<double> avg_throughput(flows.size(), EPS); // bits/ms

        // Build the sorted list of release events per flow.
        std::vector<std::multimap<long long, int>::iterator> unused; // placeholder (avoid unused warn)
        std::multimap<long long, int> release_events;                // time_ms -> flow_idx
        for (size_t i = 0; i < flows.size(); ++i)
        {
            long long period = ms_to_int(flows[i].period);
            for (long long t = 0; t < hyperperiod_ms; t += period)
            {
                release_events.insert({t, static_cast<int>(i)});
            }
        }

        // Under EXEC_TIME_MODE=sampled each transport block takes the number
        // of attempts it would really need, not the worst case, which is what
        // exposes non-preemptive scheduling anomalies.
        const AttemptSampler attempt_sampler(runtime.exec_sample_seed,
                                             sampling_execution_times(runtime));

        std::vector<MachineState> machines(HARQs);
        std::vector<ReadyItem> ready_queue;
        std::vector<DispatchEntry> dispatch_table;
        std::set<int> harq_used;

        int rb_in_use = 0;

        double current_time = 0.0;
        auto release_it = release_events.begin();

        auto next_release_time = [&]() -> std::optional<double>
        {
            if (release_it == release_events.end())
                return std::nullopt;
            return static_cast<double>(release_it->first);
        };

        auto next_machine_free_time = [&]() -> std::optional<double>
        {
            std::optional<double> best;
            for (const auto &m : machines)
            {
                if (m.busy && (!best.has_value() || m.free_at < *best))
                    best = m.free_at;
            }
            return best;
        };

        // t_up: a packet is delivered only this long after its last TB ends.
        const double t_up_ms = upper_layer_delay_ms(runtime);

        // Compute the PF metric M_i(t) for a ready item at the current time.
        auto compute_metric = [&](const ReadyItem &item, double t) -> double
        {
            const ResolvedFlow &f = flows[item.flow_idx];
            // D_i(t): inverse of the remaining time to the deadline, floored
            // at one TTI. A TTI is the smallest actionable remaining time in
            // this system -- nothing can be sent sooner -- and it keeps D
            // within about an order of magnitude of G and P, both of which
            // live in [0, 1]. The previous 0.01 ms floor let D reach 100, so
            // the delay term dominated or vanished largely regardless of the
            // alpha / beta / gamma the paper tunes. The deadline is the one
            // the TB itself has: the packet's, less the upper-layer delay
            // still to come after the TB ends.
            const double tti_ms_metric = runtime.tti_s * 1000.0;
            double remaining = std::max(tti_ms_metric, item.deadline_ms - t_up_ms - t);
            double D = 1.0 / remaining;
            double G = std::max(0.0, 1.0 - avg_throughput[item.flow_idx] / std::max(f.required_rate_bps, EPS));
            double P = f.priority_scalar;
            double U = alpha * D + beta * G + gamma * P;
            return U / std::max(avg_throughput[item.flow_idx], EPS);
        };

        while (true)
        {
            // 1) Free machines whose transmission has ended by current_time.
            for (auto &m : machines)
            {
                if (m.busy && m.free_at <= current_time + 1e-9)
                {
                    rb_in_use -= m.b_in_use;
                    m.busy = false;
                    m.b_in_use = 0;
                }
            }

            // 2) Admit any releases due at current_time.
            while (release_it != release_events.end() &&
                   static_cast<double>(release_it->first) <= current_time + 1e-9)
            {
                int idx = release_it->second;
                const ResolvedFlow &f = flows[idx];
                int release_ms = static_cast<double>(release_it->first);
                for (int s = 0; s < f.num_segments; ++s)
                {
                    // job_id identifies the job, not its arrival order, so
                    // every method faces the same run for a given seed.
                    const std::uint64_t job_id =
                        (static_cast<std::uint64_t>(idx) << 40) ^
                        (static_cast<std::uint64_t>(release_ms) << 16) ^
                        static_cast<std::uint64_t>(s);
                    const int attempts = attempt_sampler.Draw(f.bler_used, f.k, job_id);
                    const double duration =
                        tb_duration_ms(attempts, runtime);

                    ready_queue.push_back(
                        ReadyItem{idx, release_ms, release_ms + f.deadline, s, duration});
                }
                ++release_it;
            }

            // 3) One TTI of the paper's algorithm (Sec. III-C, steps 2-4):
            //    compute M_i(t) once for every ready item, sort descending,
            //    and allocate in that order -- each item on the lowest-index
            //    free HARQ process if its B fits in the free RBs, otherwise
            //    the next item is tried, so a smaller lower-priority item can
            //    still use spare RBs. R_bar does not move within the TTI; it
            //    is updated after it (step 4), as in the authors' code.
            std::vector<double> granted_bits(flows.size(), 0.0);
            std::vector<char> queued(flows.size(), 0); // flows with data this TTI
            for (const ReadyItem &item : ready_queue)
                queued[static_cast<std::size_t>(item.flow_idx)] = 1;

            if (!ready_queue.empty())
            {
                std::vector<double> metric(ready_queue.size());
                for (size_t qi = 0; qi < ready_queue.size(); ++qi)
                    metric[qi] = compute_metric(ready_queue[qi], current_time);

                std::vector<size_t> order(ready_queue.size());
                std::iota(order.begin(), order.end(), 0);
                std::sort(order.begin(), order.end(),
                          [&](size_t ia, size_t ib)
                          {
                              if (metric[ia] != metric[ib])
                                  return metric[ia] > metric[ib];
                              const ReadyItem &a = ready_queue[ia];
                              const ReadyItem &b = ready_queue[ib];
                              // Tie-break: earliest absolute deadline, then app name
                              // (kept consistent with the EDF variant's convention).
                              if (a.deadline_ms != b.deadline_ms)
                                  return a.deadline_ms < b.deadline_ms;
                              return flows[a.flow_idx].app < flows[b.flow_idx].app;
                          });

                std::vector<char> taken(ready_queue.size(), 0);
                for (size_t qi : order)
                {
                    int free_machine = -1;
                    for (size_t mi = 0; mi < machines.size(); ++mi)
                    {
                        if (!machines[mi].busy)
                        {
                            free_machine = static_cast<int>(mi);
                            break;
                        }
                    }
                    if (free_machine == -1)
                        break;

                    const ReadyItem &item = ready_queue[qi];
                    const ResolvedFlow &f = flows[item.flow_idx];
                    if (rb_in_use + f.b > RBs)
                        continue;

                    int start = whole_ms(current_time, "qospf start");
                    int end = start + whole_ms(item.duration_ms, "qospf TB duration");

                    machines[free_machine].busy = true;
                    machines[free_machine].free_at = end;
                    machines[free_machine].b_in_use = f.b;
                    rb_in_use += f.b;
                    harq_used.insert(free_machine);

                    dispatch_table.push_back(DispatchEntry{
                        f.app, f.b, item.deadline_ms, end, free_machine,
                        item.release_ms, start, item.subtask});

                    // The payload this TB carries, granted in this TTI.
                    granted_bits[static_cast<std::size_t>(item.flow_idx)] +=
                        (f.packetsize * 8.0) / f.num_segments;
                    taken[qi] = 1;
                }

                std::vector<ReadyItem> left;
                left.reserve(ready_queue.size());
                for (size_t qi = 0; qi < ready_queue.size(); ++qi)
                    if (!taken[qi])
                        left.push_back(ready_queue[qi]);
                ready_queue.swap(left);
            }

            // 4) Advance time to the next event.
            auto nr = next_release_time();
            auto nf = next_machine_free_time();

            bool have_more_releases = nr.has_value();
            bool have_busy_machines = nf.has_value();

            if (!have_more_releases && !have_busy_machines && ready_queue.empty())
            {
                break; // nothing left to do
            }

            double next_t = std::numeric_limits<double>::infinity();
            if (have_more_releases)
                next_t = std::min(next_t, *nr);
            if (have_busy_machines)
                next_t = std::min(next_t, *nf);

            if (!std::isfinite(next_t))
            {
                // Ready items exist but no machine will ever free (should
                // not happen with HARQs >= 1); stop to avoid an infinite loop.
                break;
            }

            // ------------------------------------------------------------
            // R_bar, the exponential moving average of the throughput each
            // flow achieves (Eq. 1), updated after the TTI the way the
            // authors' implementation (Simu5G's PF scheduler) does it: every
            // flow with data queued in the TTI takes the sample of the bits
            // granted to it there (0 if none),
            //
            //     R_bar_i <- (1 - lambda) R_bar_i + lambda bits_i / TTI,
            //
            // and over the TTIs until the next event a flow still holding
            // queued data takes zero samples, while a flow with nothing queued
            // keeps its average -- only flows with data are scheduled, hence
            // averaged. This is an event-driven simulation, so the zero
            // samples of the elapsed TTIs are applied in one go,
            // (1 - lambda)^n.
            // ------------------------------------------------------------
            const double tti_ms = runtime.tti_s * 1000.0;
            const long elapsed_ttis = (tti_ms > 0.0)
                ? std::max(1L, std::lround((next_t - current_time) / tti_ms))
                : 1L;

            std::vector<char> still_queued(flows.size(), 0);
            for (const ReadyItem &item : ready_queue)
                still_queued[static_cast<std::size_t>(item.flow_idx)] = 1;
            for (size_t i = 0; i < flows.size(); ++i)
            {
                if (queued[i])
                    avg_throughput[i] = (1.0 - lambda) * avg_throughput[i] +
                                        lambda * granted_bits[i] / tti_ms;
                if (still_queued[i] && elapsed_ttis > 1)
                    avg_throughput[i] *= std::pow(1.0 - lambda, static_cast<double>(elapsed_ttis - 1));
                avg_throughput[i] = std::max(avg_throughput[i], EPS);
            }

            current_time = next_t;
        }

        // --- Build combo-style output JSON ---
        //
        // Times are reported on the model's discrete grid (one TTI, Table I)
        // as well as in ms, so the online phase reads every method the same
        // way whether it came from the ILP or from a baseline.
        const double tti_ms = runtime.tti_s * 1000.0;
        const auto to_tti = [tti_ms](double ms)
        { return static_cast<long long>(std::llround(ms / tti_ms)); };

        json out;
        out["combo_index"] = 0;
        out["config"] = {
            {"m_harq", HARQs},
            {"n_rb", RBs},
            {"tti_ms", tti_ms},
            {"t_up_ms", upper_layer_delay_ms(runtime)},
            {"t_up_tti", upper_layer_delay_tti(runtime)},
            {"exec_time_mode", runtime.exec_time_mode},
            {"exec_sample_seed", runtime.exec_sample_seed},
            {"objective", "qos_pf"}};

        json flows_json = json::array();
        for (const auto &f : flows)
        {
            flows_json.push_back({{"app", f.app},
                                  {"a", f.k},
                                  {"b", f.b},
                                  {"c", f.c},
                                  {"c_tti", to_tti(f.c)},
                                  {"deadline", f.deadline},
                                  {"deadline_tti", to_tti(f.deadline)},
                                  {"m", f.m},
                                  {"num_segments", f.num_segments},
                                  {"period", f.period},
                                  {"period_tti", to_tti(f.period)},
                                  {"tbs", f.tbs}});
        }
        out["flows"] = flows_json;

        json dispatch_json = json::array();
        for (const auto &d : dispatch_table)
        {
            dispatch_json.push_back({{"app", d.app},
                                     {"b", d.b},
                                     {"deadline_tti", to_tti(d.deadline_ms)},
                                     {"end_tti", to_tti(d.end_ms)},
                                     {"release_tti", to_tti(d.release_ms)},
                                     {"start_tti", to_tti(d.start_ms)},
                                     {"deadline_ms", d.deadline_ms},
                                     {"end_ms", d.end_ms},
                                     {"machine", d.machine},
                                     {"release_ms", d.release_ms},
                                     {"start_ms", d.start_ms},
                                     {"subtask", d.subtask}});
        }
        out["dispatch_table"] = dispatch_json;

        out["hyperperiod_ms"] = hyperperiod_ms;
        out["hyperperiod_tti"] = to_tti(static_cast<double>(hyperperiod_ms));
        out["harq_used"] = static_cast<int>(harq_used.size());

        if (argc >= 4)
        {
            std::ofstream out_file(argv[3]);
            out_file << out.dump(2) << std::endl;
        }
        else
        {
            std::cout << out.dump(2) << std::endl;
        }
    }
    catch (const InfeasibleFlowSet &e)
    {
        // A verdict, not a failure: record the KO and exit 0 so a sweep keeps
        // the data point.
        const std::string output_path =
            (argc >= 4) ? argv[3] : default_output_path(argv[1], "qospf");
        write_infeasible_report(output_path, extract_combo_index(argv[1]),
                                read_runtime_config(), e.apps, "qos_pf", e.what());
        std::cerr << "[infeasible] qospf: " << e.what() << '\n';
        return 0;
    }
    catch (const std::exception &e)
    {
        std::cerr << "Error: " << e.what() << std::endl;
        return 1;
    }

    return 0;
}