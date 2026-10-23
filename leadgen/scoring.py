"""0-100 ICP fit score and the one-line 'why this lead' explanation."""


def _lerp_down(x, full_at, zero_at):
    """1.0 at or below full_at, 0.0 at or above zero_at, linear in between."""
    if x <= full_at:
        return 1.0
    if x >= zero_at:
        return 0.0
    return 1.0 - (x - full_at) / (zero_at - full_at)


def newness_credit(age, icp, unknown):
    if age is None:
        return unknown
    return _lerp_down(age, icp["target_max_age_years"], icp["newness_zero_at_years"])


def rating_credit(rating, icp):
    if rating is None:
        return 0.0
    lo = icp["min_rating"]
    if rating >= lo:
        return 1.0 if lo >= 5 else 0.7 + 0.3 * (rating - lo) / (5 - lo)
    return max(0.0, 0.5 - (lo - rating))


def review_count_credit(n, icp):
    lo, hi = icp["review_count_min"], icp["review_count_max"]
    if not n:
        return 0.0
    if lo <= n <= hi:
        return 1.0
    if n < lo:
        return 0.3 + 0.5 * n / lo
    return max(0.0, 1.0 - (n - hi) / (hi * 2))


def velocity_credit(v, icp, unknown):
    if v is None:
        return unknown
    if v <= 0:
        return 0.0
    return min(1.0, v / icp["target_review_velocity"])


def type_credit(types, icp):
    wanted = {t.lower() for t in icp["practice_types"]}
    if "any" in wanted:
        return 1.0
    if not types:
        return 0.5
    return 1.0 if wanted & set(types) else 0.0


def size_credit(count, icp, unknown):
    if not count:
        return unknown
    return 1.0 if count <= icp["max_dentists"] else max(0.0, 1.0 - 0.25 * (count - icp["max_dentists"]))


def score_lead(lead, cfg):
    """Return (score 0-100, {component: credit 0-1}). Excluded leads score 0."""
    icp, w, unk = cfg["icp"], cfg["weights"], cfg.get("unknown_credit", {})
    credits = {
        "newness": newness_credit(lead["age_years"], icp, unk.get("newness", 0.3)),
        "rating": rating_credit(lead["rating"], icp),
        "review_count": review_count_credit(lead["review_count"], icp),
        "review_velocity": velocity_credit(lead["velocity"], icp, unk.get("review_velocity", 0.3)),
        "practice_type": type_credit(lead["practice_types"], icp),
        "size": size_credit(lead["dentist_count"], icp, unk.get("size", 0.6)),
    }
    total_w = sum(w.get(k, 0) for k in credits) or 1
    score = 100.0 * sum(w.get(k, 0) * c for k, c in credits.items()) / total_w
    if lead["business_status"] == "CLOSED_TEMPORARILY":
        score *= 0.5
    if lead["exclude_reason"]:
        score = 0.0
    return round(score, 1), credits


def why_this_lead(lead, credits, cfg):
    icp = cfg["icp"]
    pros, cons = [], []

    age = lead["age_years"]
    if age is not None:
        basis = lead["age_basis"]
        if age <= icp["target_max_age_years"]:
            pros.append(f"new practice (~{age:g} yrs; {basis})")
        elif credits["newness"] < 0.4:
            cons.append(f"established ~{age:g} yrs ({basis})")
        else:
            pros.append(f"fairly young (~{age:g} yrs; {basis})")
    else:
        cons.append("age unknown")

    rating, n = lead["rating"], lead["review_count"]
    if rating is not None:
        txt = f"{rating:g}★ from {n or 0} reviews"
        (pros if credits["rating"] >= 0.7 and credits["review_count"] >= 0.8 else cons).append(txt)
        if n and n > icp["review_count_max"]:
            cons.append("review count above target range")
    else:
        cons.append("no Google rating yet")

    v = lead["velocity"]
    if v is not None:
        label = "tracked" if lead["velocity_source"] == "tracked" else "lifetime avg"
        (pros if credits["review_velocity"] >= 0.5 else cons).append(f"{v:+.1f} reviews/mo ({label})")
    if lead.get("rating_change"):
        rc = lead["rating_change"]
        (pros if rc > 0 else cons).append(f"rating {'up' if rc > 0 else 'down'} {abs(rc):g} since last check")

    if lead["dentist_count"]:
        who = f" ({lead['head_dentist']})" if lead["head_dentist"] else ""
        if lead["dentist_count"] == 1:
            pros.append(f"solo practice{who}")
        elif lead["dentist_count"] <= icp["max_dentists"]:
            pros.append(f"{lead['dentist_count']} dentists{who}")
        else:
            cons.append(f"{lead['dentist_count']} dentists listed (larger group)")

    types = lead["practice_types"]
    if types and credits["practice_type"] == 1.0 and "any" not in icp["practice_types"]:
        pros.append("/".join(sorted(types)))
    elif types and credits["practice_type"] == 0.0:
        cons.append(f"{'/'.join(sorted(types))} (outside target types)")

    if not lead["website"]:
        pros.append(f"no website listed, a strong opening for {icp.get('selling', 'your services')}")
    if lead["business_status"] == "CLOSED_TEMPORARILY":
        cons.append("temporarily closed")
    if lead["exclude_reason"]:
        return f"EXCLUDED: {lead['exclude_reason']}"

    line = "; ".join(pros) if pros else "weak fit"
    if cons:
        line += ". Watch: " + "; ".join(cons)
    return line[0].upper() + line[1:]
