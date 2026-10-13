#!/usr/bin/env python3
"""
GitLab CI Pipeline Lean Auditor
Evaluates a .gitlab-ci.yml against the 11-point lean scorecard from the
Gauntlet GitLab Primer and industry best practices.
"""

import sys
import os
import re

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


def read_file(path):
    if not os.path.exists(path):
        print(f"Error: File not found: {path}", file=sys.stderr)
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def audit_pipeline(content, filename=".gitlab-ci.yml"):
    parsed = None
    if HAS_YAML:
        try:
            parsed = yaml.safe_load(content)
        except Exception:
            parsed = None

    lines = content.splitlines()
    findings = []
    scorecard = {}

    # Check 1: workflow: rules (One pipeline per push)
    has_workflow_rules = bool(re.search(r"workflow:\s*\n\s*rules:", content))
    has_mr_dedup = bool(re.search(r"CI_OPEN_MERGE_REQUESTS", content) or re.search(r"merge_request_event", content))
    c1_ok = has_workflow_rules and has_mr_dedup
    scorecard["one_pipeline_per_push"] = {
        "ok": c1_ok,
        "title": "One pipeline per push (MR deduplication)",
        "detail": "MR pipelines win and branch pipelines skip when an open MR exists." if c1_ok
                  else "Missing workflow: rules to prevent duplicate branch and MR pipelines (doubles runner load)."
    }

    # Check 2: Stale pipeline cancellation (interruptible)
    has_interruptible = bool(re.search(r"interruptible:\s*true", content))
    scorecard["interruptible"] = {
        "ok": has_interruptible,
        "title": "Stale pipelines cancel (interruptible: true)",
        "detail": "New commits automatically cancel obsolete running pipelines." if has_interruptible
                  else "Missing interruptible: true. Old runs hog runner slots after subsequent pushes."
    }

    # Check 3: Tight timeouts
    has_timeout = bool(re.search(r"timeout:\s*(\d+)", content))
    timeout_val = int(re.search(r"timeout:\s*(\d+)", content).group(1)) if has_timeout else 60
    c3_ok = has_timeout and timeout_val <= 25
    scorecard["tight_timeout"] = {
        "ok": c3_ok,
        "title": "Tight job timeouts (<= 25m)",
        "detail": f"Timeout is set to {timeout_val}m." if c3_ok
                  else f"Timeout is {timeout_val}m or missing default. Hung jobs can burn runner hours."
    }

    # Check 4: Artifact expiration
    has_expire = bool(re.search(r"expire_in:\s*['\"]?(\d+\s*(hour|h|day|d))", content, re.IGNORECASE))
    c4_ok = has_expire
    scorecard["artifact_expiration"] = {
        "ok": c4_ok,
        "title": "Artifacts expire quickly (<= 1 day)",
        "detail": "Artifacts have short retention periods." if c4_ok
                  else "Missing or excessive expire_in. Build outputs and dependencies will bloat disk storage."
    }

    # Check 5: Shallow clone (GIT_DEPTH)
    has_git_depth = bool(re.search(r"GIT_DEPTH:\s*['\"]?(\d+)", content))
    scorecard["shallow_clone"] = {
        "ok": has_git_depth,
        "title": "Shallow git clone (GIT_DEPTH <= 20)",
        "detail": "GIT_DEPTH configured for shallow fetch." if has_git_depth
                  else "Missing GIT_DEPTH. Runners will clone full git commit history on every job."
    }

    # Check 6: Worker capping (Jest / Pytest / Go / Cargo)
    has_worker_cap = bool(
        re.search(r"--maxWorkers=[1-4]", content) or
        re.search(r"pytest.*-n\s*[1-4]", content) or
        re.search(r"go test.*-p\s*[1-4]", content) or
        re.search(r"CARGO_BUILD_JOBS", content)
    )
    scorecard["worker_capping"] = {
        "ok": has_worker_cap,
        "title": "Test workers capped to runner vCPUs (<= 2-4)",
        "detail": "Test runners pin worker processes to prevent host CPU leak and OOM." if has_worker_cap
                  else "Test workers uncapped. Tools will spawn workers matching host cores, risking Exit 137 OOM."
    }

    # Check 7: Heap / Memory ceiling
    has_heap_cap = bool(
        re.search(r"NODE_OPTIONS=.*--max-old-space-size", content) or
        re.search(r"GOMEMLIMIT", content) or
        re.search(r"MaxRAMPercentage", content) or
        re.search(r"--workerIdleMemoryLimit", content) or
        re.search(r"MALLOC_ARENA_MAX", content)
    )
    scorecard["heap_capping"] = {
        "ok": has_heap_cap,
        "title": "Heap memory ceiling configured (~75% RAM)",
        "detail": "Runtimes configured with explicit heap ceilings." if has_heap_cap
                  else "Missing explicit memory limits (NODE_OPTIONS, GOMEMLIMIT, or MaxRAMPercentage)."
    }

    # Check 8: Install once, sync everywhere
    has_install_stage = bool(re.search(r"stage:\s*install", content))
    has_needs = bool(re.search(r"needs:\s*\[.*install.*\]", content) or re.search(r"needs:\s*\n\s*-\s*install", content))
    c8_ok = has_install_stage and has_needs
    scorecard["install_once"] = {
        "ok": c8_ok,
        "title": "Install once, sync everywhere (DAG artifacts)",
        "detail": "Single install job distributes dependencies forward via short-lived artifacts." if c8_ok
                  else "Jobs reinstall dependencies repeatedly. Split install into a dedicated job with needs: [install]."
    }

    # Check 9: Prebuilt CI image
    has_prebuilt = bool(re.search(r"Dockerfile\.ci", content) or re.search(r"CI_REGISTRY_IMAGE/ci", content))
    scorecard["prebuilt_ci_image"] = {
        "ok": has_prebuilt,
        "title": "Prebuilt CI tool image (Dockerfile.ci)",
        "detail": "Tooling baked into custom image; dynamic apt-get/curl avoided." if has_prebuilt
                  else "Tools installed dynamically or using generic base images without a project Dockerfile.ci."
    }

    # Check 10: Skip unchanged code
    has_rules_changes = bool(re.search(r"changes:\s*\[", content) or re.search(r"changes:\s*\n", content))
    scorecard["skip_unchanged"] = {
        "ok": has_rules_changes,
        "title": "Skip untouched code (rules: changes:)",
        "detail": "Selective job execution based on changed file paths." if has_rules_changes
                  else "All jobs execute on every push, even for markdown/documentation edits."
    }

    # Check 11: E2E / Heavy scans off the push path
    has_heavy_scheduled = bool(
        re.search(r"CI_PIPELINE_SOURCE\s*==\s*['\"]schedule['\"]", content) or
        re.search(r"when:\s*manual", content)
    )
    scorecard["heavy_off_push_path"] = {
        "ok": has_heavy_scheduled,
        "title": "Heavy test/scans off push path (schedule/manual)",
        "detail": "Heavy E2E or security jobs scheduled or manual." if has_heavy_scheduled
                  else "E2E or slow scans may run on every branch push, bloating pipeline wall time."
    }

    passed_count = sum(1 for v in scorecard.values() if v["ok"])
    total_checks = len(scorecard)
    score = int(round((passed_count / total_checks) * 100))

    return {
        "filename": filename,
        "score": score,
        "passed": passed_count,
        "total": total_checks,
        "checks": scorecard
    }


def print_report(res):
    print("=" * 68)
    print(f"  GITLAB CI PIPELINE AUDIT REPORT: {res['filename']}")
    print(f"  LEAN SCORE: {res['score']} / 100  ({res['passed']}/{res['total']} checks passed)")
    print("=" * 68)
    print()

    for key, c in res["checks"].items():
        status_symbol = "[✓]" if c["ok"] else "[✗]"
        color_start = "\033[32m" if c["ok"] else "\033[31m"
        color_end = "\033[0m"
        print(f"{status_symbol} {c['title']}")
        print(f"    {c['detail']}")
        print()

    print("-" * 68)
    if res["score"] >= 85:
        print("Verdict: EXCELLENT. Pipeline follows lean best practices.")
    elif res["score"] >= 60:
        print("Verdict: MODERATE. Apply recommended remediation to prevent OOMs & save runner minutes.")
    else:
        print("Verdict: HIGH RISK. Pipeline is prone to OOM kills (137) and excessive runner billing.")
    print("-" * 68)


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else ".gitlab-ci.yml"
    content = read_file(target)
    result = audit_pipeline(content, target)
    print_report(result)


if __name__ == "__main__":
    main()
