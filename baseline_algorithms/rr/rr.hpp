#ifndef RR_HPP
#define RR_HPP

#include "../common/json.hpp"
#include "../common/scheduler.hpp"

using json = nlohmann::json;

namespace rr {

// Round-robin: serve flows cyclically, one transport block at a time, subject
// to the resource-block budget and HARQ availability. Flow resolution
// (segmentation, MCS selection) and job materialisation are the shared
// common/ implementations -- rr.hpp used to re-declare its own
// FlowScheduleConfig / build_flow_configs / materialize_jobs /
// select_config_for_tbs, none of which main.cpp ever called.
std::vector<DispatchEntry> run_rr_scheduler(
    const std::vector<Flow>& flows,
    std::vector<Job> jobs,
    const RuntimeConfig& runtime);

}  // namespace rr

#endif
