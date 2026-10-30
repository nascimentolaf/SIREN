#include <algorithm>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>

#include "planner.hpp"
#include "json.hpp"

using json = nlohmann::json;

// Groups the Pareto set by the segmentation it implies, so the report keeps
// the shape the ILP already consumes: one entry per distinct (N, TBS) pair,
// each carrying the MCS choices available at that segmentation.
//
// E^tb depends only on N (Eq. (3)), so it is constant within a group.
int GenerateReport(const std::vector<Flow> &flowset,
                   const std::vector<urllc::TbsTable> &tables,
                   const PlannerParams &params,
                   const std::string &filename)
{
    json report;
    report["parameters"] = {
        {"rlc_header_bytes", params.rlcHeaderBytes},
        {"a_max", params.aMax},
        {"t_tx_ms", params.txMs},
        {"t_up_ms", params.upperMs},
    };
    report["applications"] = json::array();

    int infeasibleFlows = 0;

    for (const auto &flow : flowset)
    {
        const urllc::FlowSpec spec{flow.packetSize, flow.error, flow.deadline};

        const auto considered = urllc::EnumerateConfigurations(
            spec, tables, params.rlcHeaderBytes, params.aMax, params.txMs, params.upperMs);
        const auto pareto = urllc::ParetoFilter(considered);

        json app;
        app["app"] = flow.app;
        app["packet_size"] = flow.packetSize;
        app["error"] = flow.error;
        app["period"] = flow.period;
        app["deadline"] = flow.deadline;
        app["type"] = flow.type;
        app["num_feasible"] = considered.size();
        app["num_pareto"] = pareto.size();
        app["feasible"] = !pareto.empty();
        app["segmentation_options"] = json::array();

        if (pareto.empty())
        {
            // Eq. (6): P_i empty makes F_i, and hence the flow set, infeasible.
            // Reported rather than thrown, so a schedulability sweep can count
            // this flow set as unschedulable instead of losing it to an error.
            ++infeasibleFlows;
            std::cerr
                << "[infeasible] " << flow.app
                << ": no configuration satisfies both A <= " << params.aMax
                << " and C + t_up <= " << flow.deadline << " ms (t_up = "
                << params.upperMs << " ms)\n";
        }

        // (segments, tbs) -> index into app["segmentation_options"], ordered.
        std::map<std::pair<int, int>, std::size_t> groups;

        for (const auto &c : pareto)
        {
            const auto key = std::make_pair(c.segments, c.tbs);
            auto it = groups.find(key);

            if (it == groups.end())
            {
                const auto payload =
                    static_cast<uint32_t>(c.tbs) - params.rlcHeaderBytes;

                json option;
                option["num_segments"] = c.segments;
                option["tbs"] = c.tbs;
                option["payload_per_segment"] = payload;
                option["tb_error"] = c.etbTarget;
                option["mbc_combinations"] = json::array();

                it = groups.emplace(key, app["segmentation_options"].size()).first;
                app["segmentation_options"].push_back(option);
            }

            app["segmentation_options"][it->second]["mbc_combinations"].push_back(
                {{"m", c.m},
                 {"b", c.rbs},
                 {"c", c.wcttMs},
                 {"a", c.attempts},
                 {"bler", c.bler}});
        }

        report["applications"].push_back(app);
    }

    report["num_infeasible_flows"] = infeasibleFlows;

    std::ofstream out(filename);

    if (!out)
    {
        std::cerr << "Cannot create output file\n";
        return infeasibleFlows;
    }

    out << report.dump(4);
    out.close();

    return infeasibleFlows;
}
