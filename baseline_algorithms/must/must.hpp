#ifndef MUST_HPP
#define MUST_HPP

#include "common/scheduler.hpp"

#include <cmath>
#include <deque>
#include <string>

namespace must
{

    struct Packet
    {
        std::size_t flow{};
        int index{}; // instance number within the hyperperiod
        int release_tti{};
        int deadline_tti{};   // absolute: the packet's deadline, as reported
        int latest_end_tti{}; // deadline_tti - t_up: every TB must end by then (d_p = 0)
        int next_segment{};   // first TB not yet started
        int done_segments{};
        bool dropped{false};
    };

    struct Running
    {
        std::size_t packet{};
        int segment{};
        int machine{};
        int start_tti{};
        int wcet_end_tti{};   // what the plan assumes
        int actual_end_tti{}; // when the process really frees
    };

    struct Reservation
    {
        std::size_t packet{};
        int segment{};
        int machine{};
        int start_tti{};
        int end_tti{};
    };

    struct Entry
    {
        std::string app;
        int b{};
        int release_tti{}, deadline_tti{}, start_tti{}, end_tti{}, machine{}, subtask{};
    };

    double logistic_slope(double plr, double plr_qos);

} // namespace

#endif
