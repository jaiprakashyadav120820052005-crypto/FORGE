"""Reproducible v1/v2 recommender evaluation with fixed and seeded profiles."""
import argparse
import json
import random
from pathlib import Path

from core import picker
from core.recommendation_catalog import (
    PROJECT_FACTOR_CATALOG,
    RECOMMENDATION_PROJECT_KEYS,
)
from tests.eval.personas import PERSONAS

ROOT = Path(__file__).resolve().parents[2]
BASELINES = Path(__file__).with_name("baselines")
EXPERIENCE = ["sensor_read", "soldering", "motor_driver", "breadboard", "raspberry_pi", "trained_model"]
CAREERS = ["embedded", "robotics", "vision", "iot", "undecided"]
INTERESTS = ["sensing", "robotics", "embedded", "vision", "iot", "mechanics"]
TOOLS = ["soldering_iron", "multimeter", "3d_printer", "college_lab", "laptop"]
BUDGETS = [1000, 2000, 5000, 10000, 20000, 50000, None]


def rank(profile, limit=5):
    return picker.rank_factor_projects(profile, limit=limit)


def synthetic_profiles(count=300, seed=20261022):
    rng = random.Random(seed)
    profiles = []
    for _ in range(count):
        career = rng.sample(CAREERS, rng.randint(0, 2))
        interests = rng.sample(INTERESTS, rng.randint(0, 3))
        experience = rng.sample(EXPERIENCE, rng.randint(0, len(EXPERIENCE)))
        tools = rng.sample(TOOLS, rng.randint(0, 3))
        profile = {
            "career": career,
            "interests": interests,
            "experience": experience,
            "languages": rng.sample(["python", "c_cpp"], rng.randint(0, 2)),
            "tools": tools,
            "budget_inr": rng.choice(BUDGETS),
        }
        if rng.random() < 0.35:
            profile["schedule"] = {
                "hours_per_week": rng.choice([3, 5, 8, 12, 20]),
                "deadline_weeks": rng.choice([4, 8, 12, 16, 24]),
            }
        profiles.append(profile)
    return profiles


def rank_version(profile, version, limit=5):
    if version == "v2":
        result = picker.rank_factor_projects_v2(profile, limit=limit)
        return [(item.key, item.final) for item in result["results"]]
    return rank(profile, limit=limit)


def evaluate_expectations(personas, version="v1"):
    failures = []
    for persona in personas:
        results = rank_version(persona["profile"], version)
        keys = [key for key, _ in results]
        expect = persona["expect"]
        if "max_cost_inr" in expect and any(
            PROJECT_FACTOR_CATALOG[key].get("cost_inr") is None
            or PROJECT_FACTOR_CATALOG[key]["cost_inr"] > expect["max_cost_inr"]
            for key in keys
        ):
            failures.append({"persona": persona["name"], "expectation": "max_cost_inr"})
        if "max_difficulty" in expect and any(
            PROJECT_FACTOR_CATALOG[key]["difficulty"] > expect["max_difficulty"] for key in keys
        ):
            failures.append({"persona": persona["name"], "expectation": "max_difficulty"})
        if "must_include_one_of" in expect and not set(expect["must_include_one_of"]) & set(keys):
            failures.append({"persona": persona["name"], "expectation": "must_include_one_of"})
        if "not_only_tags" in expect and keys and all(
            set(PROJECT_FACTOR_CATALOG[key]["required_skills"]) <= set(expect["not_only_tags"])
            for key in keys
        ):
            failures.append({"persona": persona["name"], "expectation": "not_only_tags"})
    return failures


def _jaccard(left, right):
    a, b = set(left), set(right)
    return len(a & b) / len(a | b) if a | b else 1.0


def run_v1():
    profiles = [persona["profile"] for persona in PERSONAS] + synthetic_profiles()
    candidate_appearances = set()
    distinct_counts, score_gaps, stability = [], [], []
    level_violations = 0
    all_equal = 0
    result_count = 0

    for profile in profiles:
        results = rank(profile)
        scores = [score for _, score in results]
        keys = [key for key, _ in results]
        candidate_appearances.update(keys)
        result_count += len(keys)
        distinct_counts.append(len(set(scores)))
        score_gaps.append(scores[0] - scores[-1] if len(scores) > 1 else 0)
        if len(scores) == 5 and len(set(scores)) == 1:
            all_equal += 1
        level = picker._student_level(profile)
        level_violations += sum(
            PROJECT_FACTOR_CATALOG[key]["difficulty"] > level + 1.5 for key in keys
        )

        budget = profile.get("budget_inr")
        if budget:
            lower = dict(profile, budget_inr=budget * 0.9)
            upper = dict(profile, budget_inr=budget * 1.1)
            low_keys = [key for key, _ in rank(lower)]
            high_keys = [key for key, _ in rank(upper)]
            stability.append(_jaccard(low_keys, high_keys))
        else:
            stability.append(1.0)

    deterministic = all(
        rank(profile) == rank(dict(profile)) for profile in profiles
    )
    persona_results = []
    for persona in PERSONAS:
        ranked = rank(persona["profile"])
        persona_results.append({
            "name": persona["name"],
            "results": [{"key": key, "score": score} for key, score in ranked],
        })
    return {
        "version": "v1",
        "baseline_note": "Recalculated after phase 1 preserved raw 0-5 ratings; any score shifts from v1_pre_data_layer are due to zero-valued ratings no longer being clamped to 1.",
        "persona_count": len(PERSONAS),
        "synthetic_profile_count": len(profiles) - len(PERSONAS),
        "candidate_count": len(RECOMMENDATION_PROJECT_KEYS),
        "coverage_percent": round(100 * len(candidate_appearances) / len(RECOMMENDATION_PROJECT_KEYS), 2),
        "never_appearing": sorted(RECOMMENDATION_PROJECT_KEYS - candidate_appearances),
        "mean_distinct_top5_scores": round(sum(distinct_counts) / len(distinct_counts), 3),
        "mean_top1_top5_gap": round(sum(score_gaps) / len(score_gaps), 3),
        "all_equal_top5_percent": round(100 * all_equal / len(profiles), 2),
        "budget_perturbation_mean_jaccard": round(sum(stability) / len(stability), 3),
        "level_violation_percent": round(100 * level_violations / result_count, 2) if result_count else 0.0,
        "deterministic": deterministic,
        "persona_expectation_failures": evaluate_expectations(PERSONAS),
        "persona_results": persona_results,
    }


def run_v2():
    profiles = [persona["profile"] for persona in PERSONAS] + synthetic_profiles()
    candidate_appearances = set()
    distinct_counts, score_gaps, stability = [], [], []
    level_violations = 0
    readiness_violations = 0
    all_equal = 0
    result_count = 0
    cold_start_count = 0

    for profile in profiles:
        response = picker.rank_factor_projects_v2(profile)
        results = response["results"]
        if response["needs_more_info"]:
            cold_start_count += 1
        scores = [round(item.final, 8) for item in results]
        keys = [item.key for item in results]
        candidate_appearances.update(keys)
        result_count += len(keys)
        distinct_counts.append(len(set(scores)))
        score_gaps.append(scores[0] - scores[-1] if len(scores) > 1 else 0)
        if len(scores) >= 2 and len(set(scores)) == 1:
            all_equal += 1
        level = picker._student_level(profile)
        level_violations += sum(
            PROJECT_FACTOR_CATALOG[item.key]["difficulty"] > level + 1.5
            for item in results
        )
        readiness_violations += sum(item.readiness < 0.6 for item in results)

        budget = profile.get("budget_inr")
        if budget:
            lower = picker.rank_factor_projects_v2(dict(profile, budget_inr=budget * 0.9))
            upper = picker.rank_factor_projects_v2(dict(profile, budget_inr=budget * 1.1))
            low_keys = [item.key for item in lower["results"]]
            high_keys = [item.key for item in upper["results"]]
            if low_keys or high_keys:
                stability.append(_jaccard(low_keys, high_keys))

    deterministic = all(
        rank_version(profile, "v2") == rank_version(dict(profile), "v2")
        for profile in profiles
    )
    persona_results = []
    for persona in PERSONAS:
        response = picker.rank_factor_projects_v2(persona["profile"])
        persona_results.append({
            "name": persona["name"],
            "needs_more_info": response["needs_more_info"],
            "missing": response["missing"],
            "results": [
                {"key": item.key, "score": round(item.final, 6), "tier": item.tier}
                for item in response["results"]
            ],
            "goal": response["goal"],
        })

    return {
        "version": "v2",
        "persona_count": len(PERSONAS),
        "synthetic_profile_count": len(profiles) - len(PERSONAS),
        "profile_count": len(profiles),
        "candidate_count": len(RECOMMENDATION_PROJECT_KEYS),
        "coverage_percent": round(100 * len(candidate_appearances) / len(RECOMMENDATION_PROJECT_KEYS), 2),
        "never_appearing": sorted(RECOMMENDATION_PROJECT_KEYS - candidate_appearances),
        "mean_distinct_top_pick_scores": round(sum(distinct_counts) / len(distinct_counts), 3),
        "mean_top1_top_pick_gap": round(sum(score_gaps) / len(score_gaps), 6),
        "all_equal_top_picks_percent": round(100 * all_equal / len(profiles), 2),
        "budget_perturbation_mean_jaccard": round(sum(stability) / len(stability), 3) if stability else None,
        "budget_perturbation_profiles": len(stability),
        "level_violation_percent": round(100 * level_violations / result_count, 2) if result_count else 0.0,
        "readiness_violation_percent": round(100 * readiness_violations / result_count, 2) if result_count else 0.0,
        "cold_start_percent": round(100 * cold_start_count / len(profiles), 2),
        "result_count": result_count,
        "deterministic": deterministic,
        "persona_expectation_failures": evaluate_expectations(PERSONAS, "v2"),
        "persona_results": persona_results,
    }


def run(version="v1"):
    if version == "v2":
        return run_v2()
    return run_v1()


def comparison_markdown(reports):
    rows = [
        ("Candidate coverage", "coverage_percent", "coverage_percent", "%"),
        ("All-equal top picks", "all_equal_top5_percent", "all_equal_top_picks_percent", "%"),
        ("Budget perturbation Jaccard", "budget_perturbation_mean_jaccard", "budget_perturbation_mean_jaccard", ""),
        ("Difficulty violations", "level_violation_percent", "level_violation_percent", "%"),
        ("Readiness violations", None, "readiness_violation_percent", "%"),
        ("Cold-start profiles", None, "cold_start_percent", "%"),
    ]
    lines = [
        "# Recommendation Model Evaluation",
        "",
        "Deterministic evaluation over 28 hand-authored personas and 300 seeded synthetic profiles.",
        "v2 returns up to four readiness-gated picks; v1 returns five baseline ranks, so coverage is not a same-K comparison.",
        "Difficulty violations use the same raw catalog difficulty threshold (estimated level + 1.5) for both versions.",
        "Score gaps are omitted from the comparison because v1 uses a 0–100 scale and v2 uses normalized component scores.",
        "",
        "| Metric | v1 | v2 |",
        "|---|---:|---:|",
    ]
    for label, left_key, right_key, suffix in rows:
        left = reports["v1"].get(left_key) if left_key else None
        right = reports["v2"].get(right_key) if right_key else None
        left_value = f"{left}{suffix}" if left is not None else "—"
        right_value = f"{right}{suffix}" if right is not None else "—"
        lines.append(f"| {label} | {left_value} | {right_value} |")
    lines.extend([
        "",
        "## Evaluation notes",
        "",
        f"- Deterministic: v1 `{reports['v1']['deterministic']}`, v2 `{reports['v2']['deterministic']}`.",
        f"- Persona expectation failures: v1 {len(reports['v1']['persona_expectation_failures'])}; v2 {len(reports['v2']['persona_expectation_failures'])}.",
        f"- v2 requested more profile context for {reports['v2']['cold_start_percent']}% of profiles.",
        f"- v2 budget stability covers {reports['v2']['budget_perturbation_profiles']} profiles with at least one pick in a perturbed run.",
        "- Catalog cost figures remain rough estimates; the evaluator does not fetch live prices.",
        "- v1 remains the default unless explicit release acceptance targets pass.",
        "",
        "## v2 persona outcomes",
        "",
        "| Persona | Status | Top projects / missing context |",
        "|---|---|---|",
    ])
    for persona in reports["v2"]["persona_results"]:
        if persona["needs_more_info"]:
            outcome = "Needs more info: " + ", ".join(persona["missing"])
        else:
            outcome = ", ".join(item["key"] for item in persona["results"]) or "No eligible picks"
        lines.append(f"| {persona['name']} | {'More info' if persona['needs_more_info'] else 'Ranked'} | {outcome} |")
    lines.extend([
        "",
        "## Candidates never recommended",
        "",
        f"- v1: {len(reports['v1']['never_appearing'])} of {reports['v1']['candidate_count']}.",
        f"- v2: {len(reports['v2']['never_appearing'])} of {reports['v2']['candidate_count']}.",
        "",
    ])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", choices=["v1", "v2", "both"], default="v1")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.version == "both":
        reports = {"v1": run_v1(), "v2": run_v2()}
        output = args.output or BASELINES / "comparison.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(reports, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        markdown_path = ROOT / "tests" / "eval" / "report.md"
        markdown_path.write_text(comparison_markdown(reports), encoding="utf-8")
        report = reports["v2"]
        print(f"v1/v2 comparison: {output.relative_to(ROOT)}")
        print(f"summary report: {markdown_path.relative_to(ROOT)}")
    else:
        report = run(args.version)
        output = args.output or BASELINES / f"{args.version}.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    print("metric                             value")
    print("---------------------------------- ------")
    for key, value in report.items():
        if key in {"persona_results", "never_appearing", "persona_expectation_failures"}:
            continue
        print(f"{key:34} {value}")
    print(f"persona expectation failures: {len(report['persona_expectation_failures'])}")
    print(f"evaluation: {output.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
