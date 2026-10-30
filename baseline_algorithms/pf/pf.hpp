#ifndef PF_HPP
#define PF_HPP

#include "../common/scheduler.hpp"

std::vector<FlowScheduleConfig> build_flow_configs(
    const std::vector<Flow>& flows, const RuntimeConfig& runtime,
    const std::vector<urllc::TbsTable>& configs); //[cite: 1]

std::vector<Job> materialize_jobs(
    const std::vector<Flow>& flows, const std::vector<FlowScheduleConfig>& flow_cfgs,
    double hyperperiod_ms, const RuntimeConfig& runtime); //[cite: 1]

std::vector<DispatchEntry> run_pf_scheduler(
    const std::vector<Flow>& flows, std::vector<Job> jobs,
    const RuntimeConfig& runtime); //[cite: 1]
#endif