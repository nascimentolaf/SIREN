#include "rr.hpp"

#include <exception>
#include <iostream>
#include <string>
#include <utility>
#include <vector>

int main(int argc, char **argv)
{
    try
    {
        if (argc < 3 || argc > 4)
        {
            std::cerr << "Usage: ./rr_scheduler <flowset.json> <mcs_configurations.json> [output.json]\n";
            return 2; // failure to stat a command line argument
        }

        const std::string flowset_path = argv[1];
        const std::string mcs_path = argv[2];
        const std::string output_path = (argc == 4) ? argv[3] : default_output_path(flowset_path, "rr");

        const RuntimeConfig runtime = read_runtime_config();
        const std::vector<Flow> flows = parse_flowset(flowset_path);
        const std::vector<urllc::TbsTable> mcs = parse_mcs_table(mcs_path, runtime.sinr_db);
        const double hyperperiod_ms = compute_hyperperiod_ms(flows);
        const std::vector<FlowScheduleConfig> flow_cfgs = build_flow_configs(flows, runtime, mcs);

        std::vector<Job> jobs = materialize_jobs(flows, flow_cfgs, hyperperiod_ms, runtime);
        const std::vector<DispatchEntry> dispatch = rr::run_rr_scheduler(flows, std::move(jobs), runtime);

        const json report = make_report(extract_combo_index(flowset_path), runtime, flows, flow_cfgs, dispatch, hyperperiod_ms, "round_robin");

        write_report(output_path, report);

        std::cout << "RR schedule written to " << output_path << '\n';

        return 0;
    }
    catch (const InfeasibleFlowSet &e)
    {
        // A verdict, not a failure: record the KO and exit 0 so a sweep keeps
        // the data point.
        const std::string output_path =
            (argc == 4) ? argv[3] : default_output_path(argv[1], "rr");
        write_infeasible_report(output_path, extract_combo_index(argv[1]),
                                read_runtime_config(), e.apps, "round_robin", e.what());
        std::cerr << "[infeasible] rr_scheduler: " << e.what() << '\n';
        return 0;
    }
    catch (const std::exception &e)
    {
        std::cerr << "[error] rr_scheduler: " << e.what() << '\n';
        return 1;
    }
}
