#ifndef QOS_PF_HPP
#define QOS_PF_HPP

#include "../common/scheduler.hpp"

std::string require_env(const std::string &name);
double require_env_double(const std::string &name);
int require_env_int(const std::string &name);

ResolvedFlow resolve_flow(const FlowSpec &flow,
                          const std::vector<urllc::TbsTable> &table,
                          const RuntimeConfig &runtime);

void assign_5qi(ResolvedFlow &rf, const qos_data &qd);
void normalize_priorities(std::vector<ResolvedFlow> &flows);

long long ms_to_int(double ms);
long long compute_hyperperiod(const std::vector<ResolvedFlow> &flows);

#endif