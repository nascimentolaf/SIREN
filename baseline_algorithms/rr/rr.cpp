#include "rr.hpp"

namespace rr
{
    namespace
    {
        struct ActiveJob
        {
            int end_ms{};
            int machine{};
            int b{};
            std::size_t job_id{};
        };

        struct ActiveCompare
        {
            bool operator()(const ActiveJob &a, const ActiveJob &b) const
            {
                if (std::abs(a.end_ms - b.end_ms) > kTimeEpsMs)
                    return a.end_ms > b.end_ms;
                return a.machine > b.machine;
            }
        };

        double round_up_to_tti(double time_ms, double tti_ms)
        {
            if (tti_ms <= 0.0)
                return time_ms;
            const double ticks = std::ceil((time_ms - kTimeEpsMs) / tti_ms);
            return std::max(0.0, ticks * tti_ms);
        }

    } // namespace

    std::vector<DispatchEntry> run_rr_scheduler(
        const std::vector<Flow> &flows, std::vector<Job> jobs,
        const RuntimeConfig &runtime)
    {
        if (runtime.harqs <= 0)
            die("HARQs must be > 0");
        if (runtime.rbs <= 0.0)
            die("RBs must be > 0");

        const double tti_ms = runtime.tti_s * 1000.0;
        if (tti_ms <= 0.0)
            die("TTI must be > 0");
        if (flows.empty() && !jobs.empty())
            die("Cannot schedule jobs without flows");

        std::vector<std::deque<std::size_t>> ready(flows.size());
        std::vector<bool> active_machine(static_cast<std::size_t>(runtime.harqs), false);
        std::priority_queue<ActiveJob, std::vector<ActiveJob>, ActiveCompare> active;
        std::vector<DispatchEntry> dispatch;

        std::size_t next_release = 0;
        std::size_t completed_jobs = 0;
        double now_ms = 0.0;
        double used_rbs = 0.0;
        std::size_t rr_pointer = 0;

        auto enqueue_releases = [&]()
        {
            while (next_release < jobs.size() && jobs[next_release].release_ms <= now_ms + kTimeEpsMs)
            {
                if (jobs[next_release].schedulable)
                {
                    ready[jobs[next_release].flow_index].push_back(next_release);
                }
                else
                {
                    ++completed_jobs; // Impossible TB: represented as unschedulable, not executed.
                }
                ++next_release;
            }
        };

        auto free_completed = [&]()
        {
            while (!active.empty() && active.top().end_ms <= now_ms + kTimeEpsMs)
            {
                const auto finished = active.top();
                active.pop();
                active_machine[static_cast<std::size_t>(finished.machine)] = false;
                used_rbs -= static_cast<double>(finished.b);
                ++completed_jobs;
            }
            if (std::abs(used_rbs) < kTimeEpsMs)
                used_rbs = 0.0;
        };

        auto lowest_free_machine = [&]() -> std::optional<int>
        {
            for (int m = 0; m < runtime.harqs; ++m)
            {
                if (!active_machine[static_cast<std::size_t>(m)])
                    return m;
            }
            return std::nullopt;
        };

        auto find_next_rr_job = [&]() -> std::optional<std::size_t>
        {
            if (flows.empty())
                return std::nullopt;
            for (std::size_t offset = 0; offset < flows.size(); ++offset)
            {
                const std::size_t fi = (rr_pointer + offset) % flows.size();
                if (ready[fi].empty())
                    continue;
                const std::size_t candidate_id = ready[fi].front();
                if (used_rbs + jobs[candidate_id].b <= runtime.rbs + kTimeEpsMs)
                    return candidate_id;
            }
            return std::nullopt;
        };

        const std::size_t total_jobs = jobs.size();
        while (completed_jobs < total_jobs)
        {
            enqueue_releases();
            free_completed();

            bool scheduled_any = false;
            while (true)
            {
                const auto machine = lowest_free_machine();
                if (!machine)
                    break;
                const auto candidate = find_next_rr_job();
                if (!candidate)
                    break;

                const std::size_t job_id = *candidate;
                Job &job = jobs[job_id];
                auto &q = ready[job.flow_index];
                if (q.empty() || q.front() != job_id)
                    die("Internal RR ready-queue inconsistency");
                q.pop_front();

                // Every TB starts only on a TTI boundary. No preemption is performed.
                const int start_ms = whole_ms(round_up_to_tti(now_ms, tti_ms), "rr start");
                if (start_ms > now_ms + kTimeEpsMs)
                {
                    // This can occur only when now_ms is a release/completion not aligned to TTI.
                    // Put the job back and advance the global clock to the valid scheduling instant.
                    q.push_front(job_id);
                    now_ms = start_ms;
                    break;
                }

                // Transmission length: the attempts the TB takes (Job::k), as
                // for every other baseline. Under EXEC_TIME_MODE=wcet (the
                // default) that is the A the report publishes for the flow;
                // under sampled it is the seeded draw of materialize_jobs(),
                // keyed by job identity, so a run is reproducible and every
                // method faces the same attempts.
                const double duration_ms = tb_duration_ms(job.k, runtime);
                if (!(duration_ms > 0.0) || !std::isfinite(duration_ms))
                    die("Computed non-positive/non-finite TB duration");

                const int end_ms = start_ms + whole_ms(duration_ms, "rr TB duration");
                active.push(ActiveJob{end_ms, *machine, job.b, job_id});
                active_machine[static_cast<std::size_t>(*machine)] = true;
                used_rbs += static_cast<double>(job.b);
                dispatch.push_back(DispatchEntry{
                    flows[job.flow_index].app, job.b, job.deadline_ms, end_ms, *machine,
                    job.release_ms, start_ms, job.segment_index});
                rr_pointer = (job.flow_index + 1) % flows.size();
                scheduled_any = true;
            }

            if (completed_jobs >= total_jobs)
                break;
            if (scheduled_any)
                continue;

            // Nothing else can start now. Jump to the next release or machine completion.
            // The event clock is a double (now_ms is), so the sentinel must be a
            // double infinity. std::numeric_limits<int>::infinity() is 0 -- int
            // has no infinity -- which made this sentinel collapse to 0, and
            // std::nextafter(now_ms, 0) then walked the clock *backwards*, so
            // now_ms never advanced and the scheduler looped forever.
            double next_time = std::numeric_limits<double>::infinity();
            if (!active.empty())
                next_time = std::min(next_time, static_cast<double>(active.top().end_ms));
            if (next_release < jobs.size())
                next_time = std::min(next_time, static_cast<double>(jobs[next_release].release_ms));
            if (!std::isfinite(next_time))
                break;
            if (next_time <= now_ms + kTimeEpsMs)
                next_time = std::nextafter(now_ms, std::numeric_limits<double>::infinity());
            now_ms = next_time;
        }

        return dispatch;
    }

} // namespace rr
