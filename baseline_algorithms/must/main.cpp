// MUST baseline -- Massive URLLC Scheduling Technique, Khorov et al., "A
// Framework to Maximize the Capacity of 5G Systems for Ultra-Reliable
// Low-Latency Communications", IEEE TMC 20(6), 2021, Section 2.1 -- wired
// into the framework's flowset pipeline.
//
//   Usage: ./must <flowset.json> <mcs_configurations.json> [output.json]
//
// Same CLI, environment variables and combo-style report as the other
// baseline schedulers.
//
// -------------------------------------------------------------------------
// The algorithm
// -------------------------------------------------------------------------
//
// At every mini-slot MUST re-plans every enqueued packet over the whole of
// its remaining lifetime:
//   1. packets are sorted by (remaining lifetime, 1/cost), the cost being the
//      gradient of a logistic utility of the flow's PLR estimate;
//   2. in that order, each packet is given the best free resource blocks
//      over the (RB x mini-slot) matrix up to its deadline, earliest column
//      first among equal rates; a packet that cannot be completed in time
//      gets nothing (its resources would be wasted);
//   3. resource blocks of the current mini-slot still free after step 2 are
//      handed out by the legacy scheduler so that packets finish earlier.
// Only the current mini-slot of the plan is executed; the plan is rebuilt at
// the next mini-slot. Packets whose lifetime expires are discarded.
//
// -------------------------------------------------------------------------
// Mapping onto this framework
// -------------------------------------------------------------------------
//
//   * Unit of work. MUST's policy is applied to the framework's transport
//     blocks: a packet is num_segments TBs, each holding b RBs on one HARQ
//     process for its A attempts (tb_duration_ms), the same job model as
//     rr/pf/qospf/dm/edf. Step 2 therefore reserves, for every remaining TB
//     of the packet, the earliest (process, start) at which b RBs and a
//     process are free for the TB's whole duration and the TB still ends by
//     the packet's deadline less the upper-layer delay t_up, which the packet
//     spends in the other layers once its last TB ends (holding neither a
//     process nor RBs). That is the lifetime d_p the gNB sees. If any TB has
//     no such slot the packet is skipped for this mini-slot and reserves
//     nothing.
//   * Mini-slot = TTI. The plan is rebuilt every TTI; TBs that start in the
//     current TTI are dispatched, the rest of the plan is discarded.
//   * Flat channel. Every RB is worth the same to a flow, so "highest-rate
//     RBs first, earliest column among equals" is "earliest feasible slot".
//     Step 3 is implemented as a work-conserving pull-forward of planned TBs
//     into the current TTI; with earliest-first placement it never finds
//     anything to move, which is stated here so nobody looks for its effect.
//   * Cost. Equal deadlines are ordered by beta_i * c_p, Lemma 1's
//     first-order gain in utility from serving the packet: c_p =
//     -F'(PLR_i - PLR^QoS_i), the logistic F of slope A_i = 1 / PLR^QoS_i,
//     and the EWMA PLR_i of Eq. (1) with beta_i = PLR^QoS_i / 10, updated on
//     every delivery (0) and discard (1). The paper has one target for every
//     flow, so beta is a common factor there and it orders by c_p alone; with
//     per-flow targets beta_i * A_i = 1/10 for every flow, so the order
//     depends on how close each flow's loss estimate is to its own target,
//     as the utility intends -- not on the reliability class. Until a flow
//     loses a packet its estimate is 0 and every flow's gain is the same
//     (computed so that it is exactly equal), so equal deadlines go by app
//     name, as in EDF.
//   * HARQ. Attempts are inside the TB duration (worst case for the plan;
//     under EXEC_TIME_MODE=sampled a TB frees its process as soon as it
//     decodes and the next TTI's plan sees that), so the paper's NACK
//     bookkeeping on the remaining size has no counterpart.
//   * Discards. As in the paper, a packet is discarded when its lifetime
//     expires (d_p = 0, i.e. at its deadline less t_up) with a TB not yet
//     started, and its flow's PLR estimate counts the loss then. Before that, step 2 finds no slot for a
//     TB that can no longer end in time, so the packet reserves nothing.
//     Every discard is reported in `dropped_packets` and counted by
//     collect_verdicts.py as a deadline miss, so the method cannot score a
//     clean run by shedding work.
//
// Not covered: the NAT traffic model and the CA priority groups of the same
// paper (Sections 2.2 and 2.3).

#include "must.hpp"

using namespace must;

int main(int argc, char **argv)
{
    try
    {
        if (argc < 3 || argc > 4)
        {
            std::cerr << "Usage: ./must <flowset.json> <mcs_configurations.json> [output.json]\n";
            return 2;
        }
        const std::string flowset_path = argv[1];
        const std::string mcs_path = argv[2];
        const std::string output_path =
            (argc == 4) ? argv[3] : default_output_path(flowset_path, "must");

        const RuntimeConfig runtime = read_runtime_config();
        const std::vector<Flow> flows = parse_flowset(flowset_path);
        const std::vector<urllc::TbsTable> mcs = parse_mcs_table(mcs_path, runtime.sinr_db);
        const double hyperperiod_ms = compute_hyperperiod_ms(flows);
        const std::vector<FlowScheduleConfig> cfgs = build_flow_configs(flows, runtime, mcs);

        const double tti_ms = runtime.tti_s * 1000.0;
        if (tti_ms <= 0.0)
            die("TTI must be > 0");
        const int B = static_cast<int>(runtime.rbs);
        const int M = runtime.harqs;
        if (B <= 0 || M <= 0)
            die("RBs and HARQs must be > 0");
        const auto to_tti = [tti_ms](double ms)
        { return static_cast<int>(std::ceil(ms / tti_ms - kTimeEpsMs)); };
        const int H = std::max(1, static_cast<int>(std::llround(hyperperiod_ms / tti_ms)));
        const int t_up = static_cast<int>(upper_layer_delay_tti(runtime));

        const AttemptSampler sampler(runtime.exec_sample_seed, sampling_execution_times(runtime));

        // ---- packets released within the hyperperiod ----------------------
        std::vector<Packet> packets;
        int horizon = H;
        for (std::size_t i = 0; i < flows.size(); ++i)
        {
            const int P = std::max(1, static_cast<int>(std::llround(flows[i].period_ms / tti_ms)));
            const int D = std::max(1, static_cast<int>(std::floor(flows[i].deadline_ms / tti_ms + kTimeEpsMs)));
            int k = 0;
            for (int r = 0; r < H; r += P, ++k)
            {
                packets.push_back(Packet{i, k, r, r + D, r + D - t_up, 0, 0, false});
                horizon = std::max(horizon, r + D);
            }
        }
        // Worst-case TB duration per flow, in TTIs (what the plan reserves).
        std::vector<int> wcet_tti(flows.size());
        for (std::size_t i = 0; i < flows.size(); ++i)
            wcet_tti[i] = std::max(1, to_tti(tb_duration_ms(cfgs[i].k, runtime)));

        // ---- per-flow PLR estimate, Eq. (1) --------------------------------
        // beta_i = PLR^QoS_i / 10, the paper's smoothing for a target PLR^QoS.
        const auto beta = [&](std::size_t i)
        { return flows[i].error / 10.0; };
        std::vector<double> plr(flows.size(), 0.0);
        auto update_plr = [&](std::size_t i, int indicator)
        {
            plr[i] = (1.0 - beta(i)) * plr[i] + beta(i) * indicator;
        };

        std::vector<Running> running;
        std::vector<Entry> dispatch;
        json dropped = json::array();
        std::vector<char> harq_used(static_cast<std::size_t>(M), 0);
        long long pending = static_cast<long long>(packets.size());

        // ---- simulation, one decision per TTI ------------------------------
        for (int t = 0; t <= horizon && pending > 0; ++t)
        {
            // Processes that have really finished.
            for (auto it = running.begin(); it != running.end();)
            {
                if (it->actual_end_tti <= t)
                {
                    Packet &p = packets[it->packet];
                    if (++p.done_segments == cfgs[p.flow].num_segments)
                    {
                        update_plr(p.flow, 0);
                        --pending;
                    }
                    it = running.erase(it);
                }
                else
                    ++it;
            }

            // Lifetime expiry (d_p = 0): a packet still holding an unstarted
            // TB when its TBs must have ended (its deadline less t_up) is
            // discarded. (One whose TBs have all started always ends by
            // then: each was placed to.)
            for (std::size_t pi = 0; pi < packets.size(); ++pi)
            {
                Packet &p = packets[pi];
                if (p.dropped || p.release_tti > t || p.next_segment >= cfgs[p.flow].num_segments)
                    continue;
                if (t >= p.latest_end_tti)
                {
                    p.dropped = true;
                    --pending;
                    update_plr(p.flow, 1);
                    dropped.push_back({{"app", flows[p.flow].app},
                                       {"instance", p.index},
                                       {"release_tti", p.release_tti},
                                       {"deadline_tti", p.deadline_tti},
                                       {"segments_not_started", cfgs[p.flow].num_segments - p.next_segment},
                                       {"dropped_at_tti", t}});
                }
            }

            // Step 1: candidates sorted by (lifetime, 1/cost).
            std::vector<std::size_t> order;
            for (std::size_t pi = 0; pi < packets.size(); ++pi)
            {
                const Packet &p = packets[pi];
                if (!p.dropped && p.release_tti <= t && p.next_segment < cfgs[p.flow].num_segments)
                    order.push_back(pi);
            }
            if (order.empty())
                continue;
            // Lemma 1: serving a packet of flow i raises the utility by
            // beta_i * c_p to first order. beta_i * A_i = 1/10 for every
            // flow, so that is the slope / 10, which keeps equal gains equal:
            // the product (PLR^QoS / 10) * (1 / PLR^QoS) * ... rounds
            // differently per target (PLR^QoS = 1e-6 came out one ulp low and
            // lost every deadline tie at PLR = 0).
            std::vector<double> cost(flows.size());
            for (std::size_t i = 0; i < flows.size(); ++i)
                cost[i] = logistic_slope(plr[i], flows[i].error) / 10.0;
            std::sort(order.begin(), order.end(), [&](std::size_t a, std::size_t b)
                      {
                          const Packet &pa = packets[a], &pb = packets[b];
                          if (pa.latest_end_tti != pb.latest_end_tti) return pa.latest_end_tti < pb.latest_end_tti;
                          if (cost[pa.flow] != cost[pb.flow]) return cost[pa.flow] > cost[pb.flow];
                          if (flows[pa.flow].app != flows[pb.flow].app) return flows[pa.flow].app < flows[pb.flow].app;
                          return pa.index < pb.index; });

            // Step 2: look-ahead reservation over [t, horizon].
            const int W = horizon + 1 - t;
            std::vector<int> rb_free(static_cast<std::size_t>(W), B);
            std::vector<std::vector<char>> busy(static_cast<std::size_t>(M), std::vector<char>(static_cast<std::size_t>(W), 0));
            for (const Running &r : running)
            {
                const int b = cfgs[packets[r.packet].flow].b;
                for (int tau = t; tau < std::min(r.wcet_end_tti, horizon + 1); ++tau)
                {
                    rb_free[static_cast<std::size_t>(tau - t)] -= b;
                    busy[static_cast<std::size_t>(r.machine)][static_cast<std::size_t>(tau - t)] = 1;
                }
            }
            auto fits = [&](int s, int len, int b, int &machine_out)
            {
                for (int tau = s; tau < s + len; ++tau)
                    if (rb_free[static_cast<std::size_t>(tau - t)] < b)
                        return false;
                for (int m = 0; m < M; ++m)
                {
                    bool ok = true;
                    for (int tau = s; tau < s + len && ok; ++tau)
                        ok = !busy[static_cast<std::size_t>(m)][static_cast<std::size_t>(tau - t)];
                    if (ok)
                    {
                        machine_out = m;
                        return true;
                    }
                }
                return false;
            };
            auto mark = [&](const Reservation &r, int sign)
            {
                const int b = cfgs[packets[r.packet].flow].b;
                for (int tau = r.start_tti; tau < r.end_tti; ++tau)
                {
                    rb_free[static_cast<std::size_t>(tau - t)] -= sign * b;
                    busy[static_cast<std::size_t>(r.machine)][static_cast<std::size_t>(tau - t)] = (sign > 0);
                }
            };

            std::vector<Reservation> plan;
            for (std::size_t pi : order)
            {
                const Packet &p = packets[pi];
                const int len = wcet_tti[p.flow];
                const int b = cfgs[p.flow].b;
                std::vector<Reservation> mine;
                bool complete = true;
                for (int seg = p.next_segment; seg < cfgs[p.flow].num_segments; ++seg)
                {
                    bool placed = false;
                    for (int s = t; s + len <= p.latest_end_tti; ++s)
                    {
                        int m = -1;
                        if (fits(s, len, b, m))
                        {
                            Reservation r{pi, seg, m, s, s + len};
                            mark(r, +1);
                            mine.push_back(r);
                            placed = true;
                            break;
                        }
                    }
                    if (!placed)
                    {
                        complete = false;
                        break;
                    }
                }
                if (complete)
                    plan.insert(plan.end(), mine.begin(), mine.end());
                else
                    for (const Reservation &r : mine)
                        mark(r, -1); // the packet gets nothing this TTI
            }

            // Step 3: pull planned TBs forward into the current TTI when a
            // process and RBs are free now (work-conserving). Earliest-first
            // placement leaves nothing to pull on a flat channel; kept for
            // fidelity and for any future channel model.
            for (Reservation &r : plan)
            {
                if (r.start_tti == t)
                    continue;
                const int len = r.end_tti - r.start_tti;
                const int b = cfgs[packets[r.packet].flow].b;
                mark(r, -1);
                int m = -1;
                if (t + len <= packets[r.packet].latest_end_tti && fits(t, len, b, m))
                {
                    r.start_tti = t;
                    r.end_tti = t + len;
                    r.machine = m;
                }
                mark(r, +1);
            }

            // Execute the current TTI of the plan.
            for (const Reservation &r : plan)
            {
                if (r.start_tti != t)
                    continue;
                Packet &p = packets[r.packet];
                if (r.segment != p.next_segment)
                    die("internal: segment order violated");
                const Flow &f = flows[p.flow];
                const FlowScheduleConfig &c = cfgs[p.flow];
                const std::uint64_t job_id =
                    (static_cast<std::uint64_t>(p.flow) << 40) ^
                    (static_cast<std::uint64_t>(std::llround(p.release_tti * tti_ms)) << 16) ^
                    static_cast<std::uint64_t>(r.segment);
                const int attempts = sampler.Draw(c.bler, c.k, job_id);
                const int actual_len = std::max(1, to_tti(tb_duration_ms(attempts, runtime)));
                running.push_back(Running{r.packet, r.segment, r.machine, t, r.end_tti, t + actual_len});
                harq_used[static_cast<std::size_t>(r.machine)] = 1;
                dispatch.push_back(Entry{f.app, c.b, p.release_tti, p.deadline_tti, t, t + actual_len, r.machine, r.segment});
                ++p.next_segment;
            }
        }

        // ---- report ----------------------------------------------------------
        json report = make_report(extract_combo_index(flowset_path), runtime, flows, cfgs, {},
                                  hyperperiod_ms, "must");
        json table = json::array();
        for (const Entry &e : dispatch)
            table.push_back({{"app", e.app},
                             {"b", e.b},
                             {"deadline_tti", e.deadline_tti},
                             {"end_tti", e.end_tti},
                             {"release_tti", e.release_tti},
                             {"start_tti", e.start_tti},
                             {"deadline_ms", e.deadline_tti * tti_ms},
                             {"end_ms", e.end_tti * tti_ms},
                             {"machine", e.machine},
                             {"release_ms", e.release_tti * tti_ms},
                             {"start_ms", e.start_tti * tti_ms},
                             {"subtask", e.subtask}});
        report["dispatch_table"] = table;
        int used = 0;
        for (char c : harq_used)
            used += c;
        report["harq_used"] = used;
        report["dropped_packets"] = dropped;
        report["n_dropped_packets"] = dropped.size();
        json plr_json = json::object();
        for (std::size_t i = 0; i < flows.size(); ++i)
            plr_json[flows[i].app] = plr[i];
        report["must"] = {{"plr_estimate", plr_json}, {"n_packets", packets.size()}};
        write_report(output_path, report);

        std::cout << "MUST schedule written to " << output_path << '\n';
        std::cout << "Packets: " << packets.size() << "  dispatched TBs: " << dispatch.size()
                  << "  dropped packets: " << dropped.size() << "  HARQ used: " << used << '\n';
        for (const auto &d : dropped)
            std::cout << "  dropped: " << d["app"].get<std::string>() << " instance " << d["instance"]
                      << " (deadline TTI " << d["deadline_tti"] << ")\n";
        return 0;
    }
    catch (const InfeasibleFlowSet &e)
    {
        const std::string output_path =
            (argc == 4) ? argv[3] : default_output_path(argv[1], "must");
        write_infeasible_report(output_path, extract_combo_index(argv[1]),
                                read_runtime_config(), e.apps, "must", e.what());
        std::cerr << "[infeasible] must: " << e.what() << '\n';
        return 0;
    }
    catch (const std::exception &e)
    {
        std::cerr << "[error] must: " << e.what() << '\n';
        return 1;
    }
}
