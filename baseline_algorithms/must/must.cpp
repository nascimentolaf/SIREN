#include "must.hpp"

namespace must
{

    double logistic_slope(double plr, double plr_qos)
    {
        // c / A, with c = -F'(delta), F(x) = 1 / (1 + exp(A x)), A = 1 / PLR^QoS.
        // A * delta is formed as a quotient so that it is exactly -1 at PLR = 0
        // whatever the target.
        const double x = (plr - plr_qos) / plr_qos;
        const double e = std::exp(x);
        return e / ((1.0 + e) * (1.0 + e));
    }

} // namespace must
