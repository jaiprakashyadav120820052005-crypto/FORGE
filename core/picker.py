"""Recommendation helper: plain scoring (no AI). Same answers (and same resume) always give the same result."""
import math
from dataclasses import dataclass, field
import math
from . import resume as resume_mod
from .data import PROJECTS
from .recommendation_catalog import PROJECT_FACTOR_CATALOG, RECOMMENDATION_PROJECT_KEYS
from .recommender_config import (
    GENERIC_SKILLS,
    INTEREST_SHORTFALL_PENALTY,
    LEVEL_EASY_FLOOR,
    LEVEL_EASY_PENALTY,
    LEVEL_HARD_PENALTY,
    LEVEL_STRETCH_MAX,
    LOW_INFORMATION_NEUTRAL,
    LEARN_SCORE_BY_NEW_SKILL_COUNT,
    LEARN_SCORE_FOR_MANY_NEW_SKILLS,
    LEARN_SKILL_LIMIT,
    MAX_FACTOR_RATING,
    MAX_MISSING_TOOLS,
    AVAILABILITY_BASE,
    AVAILABILITY_WEIGHT,
    MISSING_SKILL_DIFFICULTY_PENALTY,
    SLIGHT_BUDGET_OVERAGE,
    TIME_COMFORT_FRACTION,
    TIME_HARD_LIMIT,
    TIME_MID_FIT,
    TIME_SOFT_LIMIT,
    TOOLS_MISSING_MULTIPLIER,
    UNKNOWN_LEARN_NEUTRAL,
    W_COVERAGE,
    W_IMPACT,
    W_INTEREST,
    W_LEARN,
    BUDGET_OVERAGE_FLOOR,
    GREAT_FIT_LEVEL_MIN,
    MAX_DO_NOW_PICKS,
    MAX_PER_DOMAIN,
    MMR_DOMAIN_WEIGHT,
    MMR_FACTOR_WEIGHT,
    MMR_LAMBDA,
    MMR_SKILL_WEIGHT,
    MIN_COMPLETENESS,
    PROFILE_COMPLETENESS_WEIGHTS,
    STAR_DO_NOW_LEVEL_MIN,
    STAR_DO_NOW_READINESS_MIN,
    STAR_GOAL_MAX_UNMET_STEPS,
)
from .role_impact import CAREER_ROLE_MAP, impact_for
from .prereqs import GRAPH
from .coverage import coverage_gain
from .recommendation_catalog import PROJECT_CATALOG, STAR_KEYS


@dataclass
class ScoreResult:
    key: str
    final: float
    interest: float
    level: float
    feasibility: float
    impact: float
    readiness: float = 1.0
    coverage: float = LOW_INFORMATION_NEUTRAL
    learn: float = UNKNOWN_LEARN_NEUTRAL
    reasons: list[str] = field(default_factory=list)
    learn_skills: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    excluded: bool = False
    level_delta: float = 0.0
    tier: str = "good_fit"
    unlocks: list[str] = field(default_factory=list)
    is_star: bool = False


def build_recommender_context(profile):
    """Normalize profile evidence once for use by v2 score components."""
    resume = profile.get("resume") or {}
    user_skills = set(profile.get("experience", []))
    user_skills.update(
        item.get("id", "")
        for item in resume.get("skills", [])
        if isinstance(item, dict) and item.get("confirmed")
    )
    user_skills.discard("")
    roles = {
        CAREER_ROLE_MAP.get(tag)
        for tag in profile.get("career", [])
        if CAREER_ROLE_MAP.get(tag)
    }
    roles.update(
        CAREER_ROLE_MAP[tag]
        for tag in profile.get("interests", [])
        if CAREER_ROLE_MAP.get(tag)
    )
    targets, focus = _targets(profile)
    expressed = {}
    mappings = {
        "embedded": {"electronics": 5, "programming": 4},
        "robotics": {"electronics": 4, "fabrication": 4, "control_math": 4},
        "vision": {"ai_vision": 5, "programming": 4},
        "iot": {"wireless_iot": 5, "programming": 4},
        "sensing": {"electronics": 4, "control_math": 4},
        "mechanics": {"fabrication": 5, "control_math": 3},
    }
    for tag in focus:
        for factor, value in mappings.get(tag, {}).items():
            expressed[factor] = max(expressed.get(factor, 0), value)
    return {
        "level": _student_level(profile),
        "level_known": bool(
            user_skills
            or profile.get("languages")
            or profile.get("hardware")
        ),
        "user_skills": user_skills,
        "done": set(profile.get("completed_project_ids", [])),
        "roles": roles,
        "targets": expressed,
        "budget": profile.get("budget_inr"),
        "tools": set(profile.get("tools", [])),
        "schedule": profile.get("schedule") or {},
        "profile": profile,
    }


def profile_completeness(profile):
    """Return weighted profile completeness and the groups still missing."""
    resume = profile.get("resume") or {}
    checks = {
        "focus": bool(profile.get("career") or profile.get("interests")),
        "experience": bool(
            profile.get("experience")
            or resume.get("skills")
            or profile.get("completed_project_ids")
        ),
        "budget": profile.get("budget_inr") is not None,
        "tools": bool(profile.get("tools")),
        "schedule": bool(
            (profile.get("schedule") or {}).get("hours_per_week")
            and (profile.get("schedule") or {}).get("deadline_weeks")
        ),
    }
    total = sum(PROFILE_COMPLETENESS_WEIGHTS.values())
    score = sum(
        PROFILE_COMPLETENESS_WEIGHTS[key]
        for key, is_present in checks.items()
        if is_present
    ) / total
    labels = {
        "focus": "career direction or interests",
        "experience": "experience, resume skills, or completed projects",
        "budget": "budget",
        "tools": "available tools",
        "schedule": "weekly hours and deadline",
    }
    missing = [labels[key] for key, is_present in checks.items() if not is_present]
    return score, missing


def _effective_difficulty(project, ctx):
    missing = set(project.get("required_skills", set())) - ctx["user_skills"]
    has_skill_evidence = bool(ctx["user_skills"])
    penalty = len(missing) * MISSING_SKILL_DIFFICULTY_PENALTY if has_skill_evidence else 0
    return min(float(MAX_FACTOR_RATING), project["difficulty"] + penalty)


def _level_fit(project, ctx):
    if not ctx.get("level_known", True):
        return 1.0, 0.0
    delta = _effective_difficulty(project, ctx) - ctx["level"]
    if 0 <= delta <= LEVEL_STRETCH_MAX:
        return 1.0, delta
    if delta < 0:
        return max(LEVEL_EASY_FLOOR, 1 - LEVEL_EASY_PENALTY * abs(delta)), delta
    return max(0.0, 1 - LEVEL_HARD_PENALTY * (delta - LEVEL_STRETCH_MAX)), delta


def _interest_fit(project, ctx):
    targets = ctx["targets"]
    if not targets:
        return LOW_INFORMATION_NEUTRAL
    weights = _factor_weights(ctx["profile"])
    factors = ("electronics", "programming", "fabrication", "control_math", "ai_vision", "wireless_iot")
    weighted, total_weight = 0.0, 0.0
    for factor in factors:
        if factor not in targets:
            continue
        target = targets[factor]
        score = 1.0 if project[factor] >= target else max(
            0.0, 1 - INTEREST_SHORTFALL_PENALTY * (target - project[factor])
        )
        weighted += score * weights[factor]
        total_weight += weights[factor]
    return weighted / total_weight if total_weight else LOW_INFORMATION_NEUTRAL


def _feasibility(project, ctx):
    flags, excluded = [], False
    budget, cost = ctx["budget"], project.get("cost_inr")
    if cost is None:
        flags.append("cost_unverified")
        cost_fit = 1.0
    elif budget is None or float(budget) <= 0 or cost <= float(budget):
        cost_fit = 1.0
    elif cost <= float(budget) * SLIGHT_BUDGET_OVERAGE:
        overage = (cost / float(budget) - 1) / (SLIGHT_BUDGET_OVERAGE - 1)
        cost_fit = 1 - overage * (1 - BUDGET_OVERAGE_FLOOR)
        flags.append("slightly_over_budget")
    else:
        cost_fit, excluded = 0.0, True

    schedule = ctx["schedule"]
    hours_per_week, deadline_weeks = schedule.get("hours_per_week"), schedule.get("deadline_weeks")
    time_fit = 1.0
    if hours_per_week and deadline_weeks:
        available = float(hours_per_week) * float(deadline_weeks)
        effective_level = ctx["level"] if ctx.get("level_known", True) else 2.0
        multiplier = min(
            2.0,
            1 + 0.25 * max(0, _effective_difficulty(project, ctx) - effective_level)
            + (0.3 if effective_level < 2 else 0),
        )
        ratio = project["build_hours"] * multiplier / available
        if ratio <= TIME_COMFORT_FRACTION:
            time_fit = 1.0
        elif ratio <= TIME_SOFT_LIMIT:
            time_fit = 1 - (ratio - TIME_COMFORT_FRACTION) / (TIME_SOFT_LIMIT - TIME_COMFORT_FRACTION) * TIME_MID_FIT
        elif ratio <= TIME_HARD_LIMIT:
            time_fit = TIME_MID_FIT * (1 - (ratio - TIME_SOFT_LIMIT) / (TIME_HARD_LIMIT - TIME_SOFT_LIMIT))
        else:
            time_fit, excluded = 0.0, True

    available_tools = ctx["tools"] - {"none"}
    required = set(project.get("tools_required", set())) - {"none"}
    missing = required - available_tools
    if not available_tools:
        tools_fit = 1.0
    else:
        tools_fit = TOOLS_MISSING_MULTIPLIER ** len(missing)
        flags.extend(f"needs_tool:{tool}" for tool in sorted(missing))
        excluded = excluded or len(missing) >= MAX_MISSING_TOOLS

    availability = max(1, min(5, project.get("parts_availability_india", 3)))
    score = (cost_fit * time_fit * tools_fit) ** (1 / 3) * (
        AVAILABILITY_BASE + AVAILABILITY_WEIGHT * availability / MAX_FACTOR_RATING
    )
    return score, flags, excluded


def _learn_fit(project, ctx):
    evidence = bool(ctx["user_skills"] or (ctx["profile"].get("resume") or {}).get("skills"))
    project_skills = sorted(set(project.get("required_skills", set())))
    if not evidence:
        return UNKNOWN_LEARN_NEUTRAL, project_skills[:LEARN_SKILL_LIMIT]
    new_skills = set(project_skills) - ctx["user_skills"] - GENERIC_SKILLS
    count = len(new_skills)
    learn = LEARN_SCORE_BY_NEW_SKILL_COUNT.get(count, LEARN_SCORE_FOR_MANY_NEW_SKILLS)
    return learn, sorted(new_skills)[:LEARN_SKILL_LIMIT]


def score_project_v2(key, profile, ctx=None):
    """Score one candidate with separable v2 components; selection/readiness are added later."""
    project = PROJECT_FACTOR_CATALOG[key]
    ctx = ctx or build_recommender_context(profile)
    flags = []
    if not ctx["targets"]:
        flags.append("low_information")
    interest = _interest_fit(project, ctx)
    level, difficulty_delta = _level_fit(project, ctx)
    feasibility, feasibility_flags, excluded = _feasibility(project, ctx)
    flags.extend(feasibility_flags)
    impact = impact_for(key, project, ctx["roles"])
    coverage, coverage_flags = coverage_gain(
        key, ctx["roles"], ctx["done"], ctx["user_skills"]
    )
    flags.extend(coverage_flags)
    learn, learn_skills = _learn_fit(project, ctx)
    base = W_INTEREST * interest + W_IMPACT * impact + W_COVERAGE * coverage + W_LEARN * learn
    readiness = ctx.get(
        "readiness_by_project", {}
    ).get(key, GRAPH.readiness(key, ctx["done"], ctx["user_skills"]))
    final = base * level * feasibility * (0.4 + 0.6 * readiness)
    reasons = []
    if ctx["targets"]:
        reasons.append("Matches your stated interests")
    reasons.append("Right difficulty for your experience" if difficulty_delta <= 1 else "A challenging stretch")
    reasons.append("Fits your budget" if "slightly_over_budget" not in flags else "Slightly over budget")
    return ScoreResult(
        key=key, final=final, interest=interest, level=level,
        feasibility=feasibility, impact=impact, learn=learn,
        readiness=readiness, coverage=coverage,
        reasons=reasons[:3], learn_skills=learn_skills,
        flags=sorted(set(flags)), excluded=excluded,
        level_delta=difficulty_delta, is_star=key in STAR_KEYS,
    )


def _factor_cosine(left_key, right_key):
    factors = ("electronics", "programming", "fabrication", "control_math", "ai_vision", "wireless_iot")
    left = [PROJECT_FACTOR_CATALOG[left_key].get(name, 0) for name in factors]
    right = [PROJECT_FACTOR_CATALOG[right_key].get(name, 0) for name in factors]
    denominator = math.sqrt(sum(value * value for value in left) * sum(value * value for value in right))
    return sum(a * b for a, b in zip(left, right)) / denominator if denominator else 0.0


def _project_similarity(left_key, right_key):
    left = PROJECT_FACTOR_CATALOG[left_key]
    right = PROJECT_FACTOR_CATALOG[right_key]
    left_skills = set(left.get("required_skills", set()))
    right_skills = set(right.get("required_skills", set()))
    union = left_skills | right_skills
    skill_similarity = len(left_skills & right_skills) / len(union) if union else 1.0
    same_domain = left.get("domain") == right.get("domain")
    return (
        MMR_SKILL_WEIGHT * skill_similarity
        + MMR_DOMAIN_WEIGHT * same_domain
        + MMR_FACTOR_WEIGHT * _factor_cosine(left_key, right_key)
    )


def _mmr_select(candidates, limit=MAX_DO_NOW_PICKS):
    selected, domain_counts = [], {}
    remaining = list(candidates)
    while remaining and len(selected) < limit:
        best, best_score = None, float("-inf")
        for candidate in remaining:
            domain = PROJECT_FACTOR_CATALOG[candidate.key]["domain"]
            if domain_counts.get(domain, 0) >= MAX_PER_DOMAIN:
                continue
            redundancy = max(
                (_project_similarity(candidate.key, item.key) for item in selected),
                default=0.0,
            )
            mmr_score = candidate.final - MMR_LAMBDA * redundancy
            if mmr_score > best_score or (
                mmr_score == best_score and candidate.key < best.key
            ):
                best, best_score = candidate, mmr_score
        if best is None:
            break
        selected.append(best)
        domain = PROJECT_FACTOR_CATALOG[best.key]["domain"]
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
        remaining.remove(best)
    return selected


def select_star_project_goal(profile, exclude_keys=()):
    """Choose a Star Project goal and return every incomplete prerequisite step."""
    done = set(profile.get("completed_project_ids", []))
    excluded = set(exclude_keys)
    goal_profile = dict(profile, budget_inr=None, schedule={})
    goal_ctx = build_recommender_context(goal_profile)
    candidates = []
    for key in STAR_KEYS:
        chain = GRAPH.unmet_chain(key, done, max_len=len(GRAPH.edges))
        impact = score_project_v2(key, goal_profile, goal_ctx).impact
        candidates.append((key, chain, impact))

    remaining = [item for item in candidates if item[0] not in excluded]
    reachable = [
        item for item in remaining
        if len(item[1]) <= STAR_GOAL_MAX_UNMET_STEPS
    ]
    if reachable:
        goal_key, goal_steps, _ = max(
            reachable, key=lambda item: (item[2], -len(item[1]), item[0])
        )
        eligible = not goal_steps
    else:
        goal_key, goal_steps, _ = min(
            remaining or candidates,
            key=lambda item: (len(item[1]), -item[2], item[0]),
        )
        eligible = False
    return {"key": goal_key, "steps": goal_steps, "eligible": eligible}


def rank_factor_projects_v2(profile, limit=MAX_DO_NOW_PICKS):
    """Return readiness-gated do-now picks and one unconstrained Star Project goal."""
    completeness, missing = profile_completeness(profile)
    if completeness < MIN_COMPLETENESS:
        return {
            "results": [],
            "goal": None,
            "needs_more_info": True,
            "missing": missing,
        }
    ctx = build_recommender_context(profile)
    done, user_skills = ctx["done"], ctx["user_skills"]
    scored = []
    substitutions = {}
    for key in sorted(RECOMMENDATION_PROJECT_KEYS):
        result = score_project_v2(key, profile, ctx)
        if result.excluded:
            continue
        if result.readiness < 0.6:
            next_project = GRAPH.next_step(key, done, user_skills)
            if next_project:
                substitutions.setdefault(next_project, set()).add(key)
            continue
        if result.is_star and (
            result.readiness < STAR_DO_NOW_READINESS_MIN
            or result.level < STAR_DO_NOW_LEVEL_MIN
        ):
            continue
        scored.append(result)

    for key, unlocks in substitutions.items():
        if key in {item.key for item in scored}:
            next(item for item in scored if item.key == key).unlocks = sorted(
                set(next(item for item in scored if item.key == key).unlocks) | unlocks
            )
            continue
        replacement = score_project_v2(key, profile, ctx)
        if replacement.excluded or replacement.readiness < 0.6:
            continue
        if replacement.is_star and (
            replacement.readiness < STAR_DO_NOW_READINESS_MIN
            or replacement.level < STAR_DO_NOW_LEVEL_MIN
        ):
            continue
        replacement.tier = "build_first"
        replacement.flags = sorted(set(replacement.flags) | {"build_first"})
        replacement.unlocks = sorted(unlocks)
        replacement.reasons = ["A prerequisite step for a project you matched"]
        scored.append(replacement)

    scored.sort(key=lambda item: (-item.final, item.key))
    selected = _mmr_select(scored, min(MAX_DO_NOW_PICKS, limit))
    selected_keys = {item.key for item in selected}
    ordered_scores = sorted((item.final for item in selected), reverse=True)
    top_third_count = max(1, math.ceil(len(ordered_scores) / 3)) if ordered_scores else 0
    top_third = set(ordered_scores[:top_third_count])
    for item in selected:
        if item.tier == "build_first":
            continue
        if item.is_star or item.level_delta > 1:
            item.tier = "stretch"
        elif item.level >= GREAT_FIT_LEVEL_MIN and item.final in top_third:
            item.tier = "great_fit"
        else:
            item.tier = "good_fit"
        item.unlocks = GRAPH.unlocks(item.key)[:2]

    goal = select_star_project_goal(profile, selected_keys)
    return {
        "results": selected,
        "goal": goal,
        "needs_more_info": False,
        "missing": missing,
    }

QUESTIONS = [
 ("career", "What is your career direction?", {"Embedded systems": "embedded", "Robotics": "robotics", "ML / vision": "vision", "IoT": "iot", "Undecided": "undecided"}),
 ("experience", "Which have you done?", {"Read a sensor in code": "sensor_read", "Soldered": "soldering", "Used a motor driver": "motor_driver", "Built a breadboard circuit": "breadboard", "Used a Raspberry Pi": "raspberry_pi", "Trained a model": "trained_model"}),
 ("languages", "Which programming languages do you use?", {"C / C++": "c_cpp", "Python": "python", "None yet": "none"}),
 ("schedule", "How much time do you have?", {}),
 ("budget", "What is your project budget?", {}),
 ("tools", "What tools or lab access do you have?", {"Soldering iron": "soldering_iron", "Multimeter": "multimeter", "3D printer": "3d_printer", "College lab": "college_lab", "Laptop": "laptop"}),
 ("hardware", "Which hardware do you already own?", {"Arduino": "arduino", "ESP32": "esp32", "Raspberry Pi": "raspberry_pi", "STM32": "stm32"}),
 ("goal", "What is your goal, and are you working solo or in a team?", {"Learn": "learn", "Portfolio": "portfolio", "Internship": "internship", "Competition": "competition", "Final-year project": "final_year"}),
 ("interests", "Pick up to three areas that interest you.", {"Sensors": "sensing", "Robotics": "robotics", "Embedded": "embedded", "ML / vision": "vision", "Wireless / IoT": "iot", "Mechanics": "mechanics"}),
]
AXES = [("Difficulty", "difficulty"), ("Electronics", "electronics"), ("Programming", "programming"),
    ("Fabrication", "fabrication"), ("Control math", "control_math"), ("AI / vision", "ai_vision"),
    ("Wireless / IoT", "wireless_iot"), ("Portfolio impact", "portfolio_impact")]
RADAR_AXES = [
    *AXES,
    ("Build hours", "build_hours"),
    ("Budget fit", "cost_inr"),
    ("Parts availability", "parts_availability_india"),
    ("Tools fit", "tools_required"),
]
OPTION_MAP = {key: options for key, _, options in QUESTIONS if options}


def normalize_survey_answers(answers):
    """Convert visible survey labels into the compact, deterministic scorer profile."""
    profile = {}
    for key, options in OPTION_MAP.items():
        labels = answers.get(key, [])
        if isinstance(labels, str):
            labels = [labels]
        values = [options.get(label, label) for label in labels if label]
        if key == "tools":
            # Accept IDs/labels saved by older sessions and normalize to the current option ID.
            values = ["laptop" if value in ("laptop_only", "Laptop only") else value for value in values]
        if key == "languages" and "none" in values and len(values) > 1:
            values.remove("none")
        profile[key] = values
    schedule = answers.get("schedule", {})
    if not isinstance(schedule, dict):
        schedule = {}
    profile["schedule"] = dict(
        hours_per_week=schedule.get("hours_per_week", answers.get("hours_per_week")) or None,
        deadline_weeks=schedule.get("deadline_weeks", answers.get("deadline_weeks")) or None,
    )
    profile["budget_inr"] = answers.get("budget_inr") or None
    profile["dream_job"] = str(answers.get("dream_job", "")).strip()[:160]
    profile["team"] = answers.get("team", "")
    return profile

def _mx(A, k, d):
    v = [x for x in A.get(k, []) if isinstance(x, (int, float))]
    return max(v) if v else d

def _c(x): return max(0, min(100, x))

def effective(A, R=None):
    """Survey answers win. A resume only fills the questions the user left blank (skill level, interests)."""
    E = {k: list(v) for k, v in A.items()}
    if R:
        if not E.get("skill") and R.get("level"): E["skill"] = [R["level"]]
        if not E.get("int") and R.get("interests"): E["int"] = list(R["interests"])
    return E

def fits(key, A, R=None):
    A = effective(A, R)
    p, cap = PROJECTS[key], {1: 4, 2: 7, 3: 10}[_mx(A, "skill", 2)]
    T, B = _mx(A, "time", 14), _mx(A, "budget", 5000)
    want = 8 if "ai" in A.get("int", []) or "ai" in A.get("course", []) else 4
    f = dict(diff=100 - max(0, cap - p["diff"] - 3) * 5 if p["diff"] <= cap else 100 - (p["diff"] - cap) * 25,
             time=100 if p["days"] <= T else 100 - (p["days"] - T) / T * 100,
             cost=100 if p["cost"] <= B else 100 - (p["cost"] - B) / B * 100,
             ai=100 - abs(p["ai"] - want) * 12, parts=p["parts"] * 10, demo=p["demo"] * 10, hack=p["hack"] * 10)
    return {k: round(_c(v)) for k, v in f.items()}

def score(key, A, R=None):
    A = effective(A, R)
    f, win = fits(key, A), "win" in A.get("goal", [])
    w = dict(diff=1.5, time=1.5, cost=1.5, ai=1, parts=1, demo=1.6 if win else 1, hack=1.6 if win else 1)
    base = sum(f[k] * w[k] for k in f) / sum(w.values())
    it = set(A.get("int", [])) | {{"mech": "mechanics", "ai": "ai"}.get(c) for c in A.get("course", [])}
    x = _c(base + min(8, len(set(PROJECTS[key]["tags"]) & it) * 4))
    # The resume closes up to half of the remaining gap to 100, so strong matches stay ordered instead of all hitting the ceiling.
    return round(x + (100 - x) * 0.5 * resume_mod.affinity(key, R))

def rank(A, R=None):
    return sorted(((k, score(k, A, R)) for k in PROJECTS), key=lambda x: -x[1])


def _date_weeks(value):
    if not value:
        return None
    from datetime import date, datetime
    try:
        deadline = value if isinstance(value, date) else date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return max(0.0, (deadline - datetime.now().date()).days / 7)


def _available_weeks(schedule):
    if schedule.get("deadline_weeks"):
        return float(schedule["deadline_weeks"])
    return _date_weeks(schedule.get("finish_by"))


def _profile_skills(profile):
    skills = set(profile.get("experience", []))
    for item in (profile.get("resume") or {}).get("skills", []):
        if isinstance(item, dict) and item.get("confirmed"):
            skills.add(item.get("id", ""))
    return skills


def _student_level(profile):
    skills = _profile_skills(profile)
    languages = set(profile.get("languages", []))
    hardware = set(profile.get("hardware", []))
    signal = len(skills) * 0.45 + len(languages - {"none"}) * 0.3 + len(hardware) * 0.2
    return min(5.0, 1.0 + signal)


def _targets(profile):
    targets = dict(electronics=2.5, programming=2.5, fabrication=2.5,
                   control_math=2.5, ai_vision=2.5, wireless_iot=2.5, portfolio_impact=3.0)
    mappings = {
        "embedded": {"electronics": 5, "programming": 4},
        "robotics": {"electronics": 4, "fabrication": 4, "control_math": 4},
        "vision": {"ai_vision": 5, "programming": 4},
        "iot": {"wireless_iot": 5, "programming": 4},
        "sensing": {"electronics": 4, "control_math": 4},
        "mechanics": {"fabrication": 5, "control_math": 3},
    }
    focus = set(profile.get("career", [])) | set(profile.get("interests", []))
    research = profile.get("career_research") or {}
    focus |= set(research.get("signals", []))
    for item in (profile.get("resume") or {}).get("projects", []):
        if item.get("confirmed") and item.get("domain") in mappings:
            focus.add(item["domain"])
    for tag in focus:
        for factor, value in mappings.get(tag, {}).items():
            targets[factor] = max(targets[factor], value)
    if "portfolio" in profile.get("goal", []) or "internship" in profile.get("goal", []):
        targets["portfolio_impact"] = 5
    elif "learn" in profile.get("goal", []):
        targets["portfolio_impact"] = 3
    return targets, focus


def eligibility(project_key, profile):
    """Return hard-limit eligibility and reasons; missing limits remain unknown, not assumed."""
    project = PROJECT_FACTOR_CATALOG[project_key]
    reasons = []
    budget = profile.get("budget_inr")
    cost = project["cost_inr"]
    owned = set(profile.get("hardware", []))
    controller_credit = 450 if project_key != "bluetooth_car" and "arduino" in owned else 0
    controller_credit = max(controller_credit, 500 if project_key == "bluetooth_car" and "esp32" in owned else 0)
    cost -= min(cost, controller_credit)
    if budget is not None and cost > float(budget):
        reasons.append(f"Estimated cost ₹{cost:,} exceeds your ₹{float(budget):,.0f} budget")

    schedule = profile.get("schedule") or {}
    weeks = _available_weeks(schedule)
    hours_per_week = schedule.get("hours_per_week")
    if weeks is not None and hours_per_week:
        available = max(0.0, weeks * float(hours_per_week))
        if project["build_hours"] > available:
            reasons.append(f"Needs about {project['build_hours']} hours; your deadline allows about {available:.0f}")

    selected_tools = {"laptop" if tool == "laptop_only" else tool for tool in profile.get("tools", [])}
    if selected_tools:
        available_tools = selected_tools | {"laptop"}
        if "college_lab" in selected_tools:
            available_tools |= {"soldering_iron", "multimeter", "3d_printer"}
    else:
        available_tools = None
    missing_tools = project["tools_required"] - available_tools if available_tools is not None else set()
    if missing_tools:
        names = {"soldering_iron": "soldering iron", "multimeter": "multimeter", "3d_printer": "3D printer", "laptop": "laptop"}
        reasons.append("Missing required tools: " + ", ".join(sorted(names.get(x, x) for x in missing_tools)))
    return dict(eligible=not reasons, reasons=reasons, estimated_cost_inr=cost, missing_tools=missing_tools)


def factor_breakdown(project_key, profile):
    """Fit scores for all 12 declared project factors, each normalized to 0-100."""
    project = PROJECT_FACTOR_CATALOG[project_key]
    targets, focus = _targets(profile)
    current_level = _student_level(profile)
    ideal_difficulty = min(5.0, current_level + 0.8)
    difficulty_delta = project["difficulty"] - ideal_difficulty
    difficulty_fit = 100 - (34 * difficulty_delta if difficulty_delta > 0 else 9 * abs(difficulty_delta))
    missing_skills = project["required_skills"] - _profile_skills(profile)
    if profile.get("experience") or (profile.get("resume") or {}).get("skills"):
        difficulty_fit = min(difficulty_fit, 100 - min(80, 18 * len(missing_skills)))
    result = {"difficulty": round(_c(difficulty_fit))}
    for factor in ("electronics", "programming", "fabrication", "control_math", "ai_vision", "wireless_iot", "portfolio_impact"):
        delta = abs(project[factor] - targets[factor])
        result[factor] = round(_c(100 - delta * 18))

    schedule = profile.get("schedule") or {}
    weeks = _available_weeks(schedule)
    available = weeks * float(schedule["hours_per_week"]) if weeks is not None and schedule.get("hours_per_week") else None
    result["build_hours"] = round(_c(100 - (project["build_hours"] / available * 25))) if available and available > 0 else 75
    budget = profile.get("budget_inr")
    cost = eligibility(project_key, profile)["estimated_cost_inr"]
    result["cost_inr"] = round(_c(100 - cost / float(budget) * 25)) if budget and float(budget) > 0 else 75
    result["parts_availability_india"] = project["parts_availability_india"] * 20
    result["tools_required"] = 100 if eligibility(project_key, profile)["eligible"] else 0
    return result


def _factor_weights(profile):
    weights = dict(difficulty=1.7, electronics=1.0, programming=1.0, fabrication=0.8,
                   control_math=0.9, ai_vision=0.8, wireless_iot=0.8, portfolio_impact=1.0,
                   build_hours=0.8, cost_inr=1.0, parts_availability_india=0.8, tools_required=0.7)
    _, focus = _targets(profile)
    boosts = {"embedded": {"electronics": 0.4, "programming": 0.3},
              "robotics": {"fabrication": 0.3, "control_math": 0.3},
              "vision": {"ai_vision": 0.5, "programming": 0.3},
              "iot": {"wireless_iot": 0.5, "programming": 0.2},
              "sensing": {"electronics": 0.3, "control_math": 0.2},
              "mechanics": {"fabrication": 0.4}}
    for tag in focus:
        for factor, value in boosts.get(tag, {}).items():
            weights[factor] += value
    if "competition" in profile.get("goal", []):
        weights["portfolio_impact"] += 0.6
    if profile.get("team") == "Team":
        weights["portfolio_impact"] += 0.25
    return weights


def factor_score(project_key, profile):
    fit = factor_breakdown(project_key, profile)
    weights = _factor_weights(profile)
    return round(sum(fit[key] * weights[key] for key in weights) / sum(weights.values()))


def rank_factor_projects(profile, limit=5):
    """Filter hard constraints, then deterministically rank and diversify by project domain."""
    candidates = [(key, factor_score(key, profile)) for key in RECOMMENDATION_PROJECT_KEYS
                  if eligibility(key, profile)["eligible"]]
    candidates.sort(key=lambda item: (-item[1], item[0]))
    selected, domain_counts = [], {}
    for item in candidates:
        domain = PROJECT_FACTOR_CATALOG[item[0]]["domain"]
        if domain_counts.get(domain, 0) >= 2:
            continue
        selected.append(item)
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
        if len(selected) >= limit:
            break
    return selected

def _sector(cx, cy, r0, r1, a0, a1):
    """SVG path for one ring segment between radii r0..r1 and angles a0..a1 (radians, clockwise)."""
    P = lambda r, a: f"{cx + r * math.cos(a):.1f} {cy + r * math.sin(a):.1f}"
    return f"M{P(r1, a0)} A{r1:.1f} {r1:.1f} 0 0 1 {P(r1, a1)} L{P(r0, a1)} A{r0:.1f} {r0:.1f} 0 0 0 {P(r0, a0)}Z"


def _tone(v):
    return ("#34D399", "#059669") if v >= 70 else ("#FBBF24", "#B45309") if v >= 45 else ("#FB7185", "#BE123C")


def chart_html(f, overall=None, title="FIT PROFILE", axes=None):
    """Radial 'scanner' chart. One wedge per attribute built from 10 glowing segments (each lit segment = 10% fit), a dashed radar outline across
    the wedges, a rotating scan line and HUD text. f = {axis key: 0-100}. Pure SVG + CSS (no external files); animations respect reduced-motion."""
    axes = axes or AXES
    C, R0, R1, SEG, n = 230, 50, 170, 10, len(axes)
    span, gap, th = 2 * math.pi / n, math.radians(3.5), (R1 - R0) / SEG
    s = (f'<svg viewBox="-110 0 680 460" width="100%" role="img" aria-label="{title}"><defs>'
         '<filter id="gl" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="2.6" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>'
         '<radialGradient id="core"><stop offset="0" stop-color="#4C1D95"/><stop offset="1" stop-color="#0A0F1E"/></radialGradient>'
         '<linearGradient id="sw" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#06B6D4" stop-opacity="0"/><stop offset="1" stop-color="#06B6D4" stop-opacity=".35"/></linearGradient></defs>')
    for r in (R0, R0 + (R1 - R0) * .5, R1, R1 + 10):   # reference rings and the tick ring
        dash = ' stroke-dasharray="2 5"' if r < R1 else ""
        s += f'<circle cx="{C}" cy="{C}" r="{r:.1f}" fill="none" stroke="#1E2A4A" stroke-width="1"{dash}/>'
    for d in range(0, 360, 6):                           # degree ticks
        a, ln = math.radians(d), 7 if d % 30 == 0 else 3
        s += (f'<line x1="{C + (R1 + 10) * math.cos(a):.1f}" y1="{C + (R1 + 10) * math.sin(a):.1f}" x2="{C + (R1 + 10 + ln) * math.cos(a):.1f}" '
              f'y2="{C + (R1 + 10 + ln) * math.sin(a):.1f}" stroke="{"#06B6D4" if d % 30 == 0 else "#1E2A4A"}" stroke-width="1"/>')
    pts, lab = [], ""
    for i, (name, k) in enumerate(axes):
        v = f[k]; a0 = -math.pi / 2 + i * span - span / 2 + gap / 2; a1 = a0 + span - gap; am = (a0 + a1) / 2
        lit, (c1, c2) = round(v / 100 * SEG), _tone(v)
        s += '<g class="ax">'
        for j in range(SEG):
            on, r0, r1 = j < lit, R0 + j * th + 1, R0 + (j + 1) * th - 1
            fill = (c1 if j >= lit * .5 else c2) if on else "#111A30"
            s += (f'<path d="{_sector(C, C, r0, r1, a0, a1)}" fill="{fill}" fill-opacity="{1 if on else .6}" class="{"seg" if on else "off"}"'
                  f'{" filter=\"url(#gl)\"" if on else ""} style="animation-delay:{i * 90 + j * 45}ms"/>')
        s += "</g>"
        rr = R0 + v / 100 * (R1 - R0); pts.append((C + rr * math.cos(am), C + rr * math.sin(am)))
        ux, uy = math.cos(am), math.sin(am); lx, ly = C + ux * (R1 + 34), C + uy * (R1 + 34)
        an = "start" if ux > .3 else "end" if ux < -.3 else "middle"; dy = 8 if uy > .3 else -4 if uy < -.3 else 3
        lab += (f'<text x="{lx:.1f}" y="{ly + dy:.1f}" text-anchor="{an}" class="lb">{name}</text>'
                f'<text x="{lx:.1f}" y="{ly + dy + 13:.1f}" text-anchor="{an}" class="vl" fill="{c1}">{v}%</text>')
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    s += (f'<polygon class="radar" points="{poly}" fill="#06B6D4" fill-opacity=".07" stroke="#67E8F9" stroke-width="1.4" stroke-dasharray="4 3" filter="url(#gl)"/>'
          + "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.6" fill="#fff"/>' for x, y in pts))
    sx, sy = C + (R1 + 10) * math.sin(math.radians(28)), C - (R1 + 10) * math.cos(math.radians(28))
    s += (f'<g class="sweep"><path d="M{C} {C} L{C} {C - R1 - 10} A{R1 + 10} {R1 + 10} 0 0 1 {sx:.1f} {sy:.1f}Z" fill="url(#sw)"/>'
          f'<line x1="{C}" y1="{C}" x2="{C}" y2="{C - R1 - 10}" stroke="#67E8F9" stroke-width="1.2" opacity=".7"/></g>')
    mid = overall if overall is not None else round(sum(f[k] for _, k in axes) / n)
    s += (f'<circle cx="{C}" cy="{C}" r="{R0 - 8}" fill="url(#core)" stroke="#06B6D4" stroke-width="1.2" filter="url(#gl)"/>'
          f'<text x="{C}" y="{C + 4}" text-anchor="middle" class="big">{mid}</text><text x="{C}" y="{C + 17}" text-anchor="middle" class="sm">MATCH</text>'
          f'{lab}<text x="-100" y="18" class="hud">// {title}</text><text x="-100" y="31" class="hud dim">SCAN {n} AXES · OK</text>'
          f'<text x="560" y="452" text-anchor="end" class="hud dim">FORGE</text></svg>')
    css = ('<style>body{margin:0;background:transparent}svg{display:block;max-width:680px;margin:auto}'
           'text{font-family:ui-monospace,"JetBrains Mono",Menlo,monospace}.lb{fill:#E2E8F0;font-size:10.5px;letter-spacing:.12em;text-transform:uppercase}.vl{font-size:13px;font-weight:700}'
           '.big{fill:#fff;font-size:22px;font-weight:700;font-family:Inter,sans-serif}.sm{fill:#67E8F9;font-size:8px;letter-spacing:.2em}'
           '.hud{fill:#06B6D4;font-size:10px;letter-spacing:.12em}.dim{fill:#64748B}'
           '.seg{opacity:0;animation:on .35s ease-out forwards}@keyframes on{from{opacity:0}to{opacity:1}}'
           '.radar{opacity:0;animation:on .8s ease-out 1.2s forwards}'
           '.sweep{transform-origin:230px 230px;animation:spin 7s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}'
           '@media(prefers-reduced-motion:reduce){.seg,.radar{animation:none;opacity:1}.sweep{animation:none}}</style>')
    return css + s
