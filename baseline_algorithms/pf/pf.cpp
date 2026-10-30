#include "pf.hpp"

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

    struct PfCandidate
    {
        std::size_t job_id{};
        double metric{};
        std::size_t flow_index{};
        std::size_t instance_index{};
        int segment_index{};
        double useful_bytes{};
        double duration_ms{};
    };

    double round_up_to_tti(double time_ms, double tti_ms)
    {
        if (tti_ms <= 0.0)
            return time_ms;
        const double ticks = std::ceil((time_ms - kTimeEpsMs) / tti_ms);
        return std::max(0.0, ticks * tti_ms);
    }

    std::size_t segment_useful_bytes(const Flow &flow, const Job &job,
                                     std::size_t kHeaderBytes)
    {
        const std::size_t total_bytes = flow.packet_size_bytes + kHeaderBytes;
        const std::size_t offset = static_cast<std::size_t>(job.segment_index) *
                                   static_cast<std::size_t>(job.tbs);
        const std::size_t remaining = (offset < total_bytes) ? (total_bytes - offset) : 0;
        const std::size_t segment_total = std::min<std::size_t>(remaining, static_cast<std::size_t>(job.tbs));
        const std::size_t header_for_segment = (job.segment_index == 0)
                                                   ? std::min<std::size_t>(kHeaderBytes, segment_total)
                                                   : 0;
        return segment_total - header_for_segment;
    }

} // end anonymous namespace

std::vector<DispatchEntry> run_pf_scheduler(
    const std::vector<Flow> &flows, std::vector<Job> jobs,
    const RuntimeConfig &runtime)
{
    if (runtime.harqs <= 0)
        die("HARQs must be > 0");
    if (runtime.rbs <= 0.0)
        die("RBs must be > 0");
    if (!(runtime.pf_alpha > 0.0 && runtime.pf_alpha <= 1.0))
        die("PF_ALPHA must be in (0,1]");
    if (!(runtime.pf_initial_throughput_bytes_per_ms > 0.0) ||
        !std::isfinite(runtime.pf_initial_throughput_bytes_per_ms))
    {
        die("PF_INITIAL_THROUGHPUT must be finite and > 0");
    }

    const double tti_ms = runtime.tti_s * 1000.0;
    if (tti_ms <= 0.0)
        die("TTI must be > 0");

    std::vector<std::deque<std::size_t>> ready(flows.size());
    std::vector<bool> active_machine(static_cast<std::size_t>(runtime.harqs), false);
    std::priority_queue<ActiveJob, std::vector<ActiveJob>, ActiveCompare> active;
    std::vector<DispatchEntry> dispatch;
    // R_bar_i: the past average achieved rate, in bytes/ms.
    //
    // This was previously two separate EMAs (bytes and service time) whose
    // ratio stood in for R_bar. That cannot express the half of the update
    // that matters: decaying both by the same factor leaves the ratio
    // unchanged, so an unserved flow's average never fell and it could never
    // climb the ranking. The survey defines a single EMA over the rate,
    //     R_i(t) = (1 - 1/t_c) R_i(t-1) + (1/t_c) r_i(t),
    // updated every TTI with r_i(t) = 0 when the flow is not served, which is
    // exactly what gives "the user with the lowest data rate in the previous
    // TTI the greatest priority in the current TTI".
    std::vector<double> avg_rate(flows.size(), runtime.pf_initial_throughput_bytes_per_ms);
    // Rate each flow achieved in the current TTI: the sample R_bar takes once
    // the TTI is over (0 for a flow not served).
    std::vector<double> served_rate(flows.size(), 0.0);

    std::size_t next_release = 0;
    std::size_t completed_jobs = 0;
    double now_ms = 0.0;
    double used_rbs = 0.0;

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
                ++completed_jobs;
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

    auto evaluate_candidates = [&]() -> std::vector<PfCandidate>
    {
        std::vector<PfCandidate> candidates;
        for (std::size_t fi = 0; fi < flows.size(); ++fi)
        {
            if (ready[fi].empty())
                continue;
            const std::size_t job_id = ready[fi].front();
            const Job &job = jobs[job_id];
            if (used_rbs + job.b > runtime.rbs + kTimeEpsMs)
                continue;

            // The worst-case duration, A_i(K) attempts: all the scheduler can
            // know when it decides. Under EXEC_TIME_MODE=sampled Job::k holds
            // the attempts the TB will really take, which ranking on would
            // be clairvoyant; under wcet the two are equal.
            const double duration_ms = tb_duration_ms(job.attempts_wcet, runtime);
            if (!(duration_ms > 0.0) || !std::isfinite(duration_ms))
            {
                die("Computed non-positive/non-finite PF TB duration");
            }

            // r_i,j is the *achievable* transmission rate, per the survey.
            // It used to be the outcome of a Bernoulli draw taken here, in
            // the evaluation pass: a candidate that lost the round had
            // already consumed randomness and was re-rolled on the next pass,
            // and a TB whose draw failed contributed a rate of zero, so the
            // metric was decided by a lottery rather than by the channel.
            const double useful = static_cast<double>(segment_useful_bytes(flows[fi], job, runtime.rlc_header_bytes));
            const double instantaneous_rate = useful / duration_ms;
            // R_bar as it stood at the start of the TTI: it is updated once
            // the TTI is over, so every candidate of the TTI faces the same.
            const double metric = instantaneous_rate / std::max(avg_rate[fi], kTimeEpsMs);

            candidates.push_back(PfCandidate{
                job_id, metric, fi, job.instance_index, job.segment_index,
                useful, duration_ms});
        }
        return candidates;
    };

    const std::size_t total_jobs = jobs.size();
    while (completed_jobs < total_jobs)
    {
        enqueue_releases();
        free_completed();

        const double scheduling_time = round_up_to_tti(now_ms, tti_ms);
        if (scheduling_time > now_ms + kTimeEpsMs)
            now_ms = scheduling_time;

        // One TTI: the best candidate takes the lowest free process, then
        // the candidates are taken afresh -- a flow just served offers its
        // next TB -- until no process is free or nothing fits. It used to
        // take one snapshot of each flow's first TB per event, so a flow's
        // second TB waited for the next release or completion, possibly
        // milliseconds later, with a process and the RBs it needed idle.
        bool scheduled_any = false;
        while (true)
        {
            const auto machine = lowest_free_machine();
            if (!machine)
                break;
            std::vector<PfCandidate> remaining = evaluate_candidates();
            if (remaining.empty())
                break;

            auto best_it = remaining.end();
            for (auto it = remaining.begin(); it != remaining.end(); ++it)
            {
                if (used_rbs + jobs[it->job_id].b > runtime.rbs + kTimeEpsMs)
                    continue;
                if (best_it == remaining.end() || it->metric > best_it->metric + kTimeEpsMs ||
                    (std::abs(it->metric - best_it->metric) <= kTimeEpsMs &&
                     std::tie(it->flow_index, it->instance_index, it->segment_index) <
                         std::tie(best_it->flow_index, best_it->instance_index, best_it->segment_index)))
                {
                    best_it = it;
                }
            }
            if (best_it == remaining.end())
                break;

            const PfCandidate chosen = *best_it;

            Job &job = jobs[chosen.job_id];
            auto &q = ready[job.flow_index];
            if (q.empty() || q.front() != chosen.job_id)
                die("Internal PF ready-queue inconsistency");
            q.pop_front();

            const int start_ms = whole_ms(round_up_to_tti(now_ms, tti_ms), "pf start");
            if (start_ms > now_ms + kTimeEpsMs)
            {
                q.push_front(chosen.job_id);
                now_ms = start_ms;
                break;
            }

            // The process is held for the attempts the TB really takes
            // (Job::k: the sampled draw, or the worst case under wcet).
            const int end_ms = start_ms + whole_ms(tb_duration_ms(job.k, runtime), "pf TB duration");
            active.push(ActiveJob{end_ms, *machine, job.b, chosen.job_id});
            active_machine[static_cast<std::size_t>(*machine)] = true;
            used_rbs += static_cast<double>(job.b);
            dispatch.push_back(DispatchEntry{
                flows[job.flow_index].app, job.b, job.deadline_ms, end_ms, *machine,
                job.release_ms, start_ms, job.segment_index});

            // r_i(t): the rate this flow achieves in the TTI, summed over the
            // TBs it starts here; R_bar takes it once the TTI is over.
            served_rate[job.flow_index] += chosen.useful_bytes / chosen.duration_ms;
            scheduled_any = true;
        }

        if (completed_jobs >= total_jobs)
            break;

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
        {
            next_time = std::nextafter(now_ms, std::numeric_limits<double>::infinity());
        }

        // The survey's update, once per TTI: this TTI's sample r_i(t) (0 for
        // a flow not served), then the run of zero-rate TTIs until the next
        // scheduling event, which compounds to (1 - alpha)^n.
        const long elapsed_ttis =
            std::max(1L, std::lround((next_time - now_ms) / tti_ms));
        const double alpha = runtime.pf_alpha;
        for (std::size_t fi = 0; fi < flows.size(); ++fi)
        {
            avg_rate[fi] = (1.0 - alpha) * avg_rate[fi] + alpha * served_rate[fi];
            if (elapsed_ttis > 1)
                avg_rate[fi] *= std::pow(1.0 - alpha, static_cast<double>(elapsed_ttis - 1));
            avg_rate[fi] = std::max(avg_rate[fi], kTimeEpsMs);
            served_rate[fi] = 0.0;
        }

        now_ms = next_time;
        (void)scheduled_any;
    }

    return dispatch;
}