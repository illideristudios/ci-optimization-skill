#!/usr/bin/env python3
"""
GitLab CI Pipeline Lean Auditor
Evaluates a .gitlab-ci.yml against the 11-point lean scorecard from the
Gauntlet GitLab Primer and industry best practices.

Exit 0 when every applicable check passes, 1 when one fails, 2 when the
file cannot be read or holds no jobs.
"""

import argparse
import re
import sys

# shortcut: matches comment-stripped, block-style YAML text; flow-style mappings, anchors,
# extends: and include:d files are not resolved. Upgrade to a YAML parser when they matter.

DEFAULT_TIMEOUT_MIN = 15
ARTIFACT_MAX_MIN = 24 * 60
GIT_DEPTH_MAX = 20
HEAP_FRACTION = 0.75

UNIT_MINUTES = {"s": 1 / 60, "m": 1, "h": 60, "d": 1440, "w": 10080, "mo": 43200, "y": 525600}
MIB = {"": 1 / 2**20, "b": 1 / 2**20, "k": 1 / 2**10, "m": 1, "g": 2**10}

TEST_RUNNER = r"\b(?:jest|vitest|playwright test|pytest|go test)\b"
NODE_STACK = r"\b(?:npm|npx|pnpm|yarn|node|jest|vitest)\b"
INSTALL = r"\b(?:npm ci|npm install|npm i|pnpm install|yarn install|uv sync|pip install|poetry install|go mod download|bundle install)\b"
TOOL_INSTALL = r"\b(?:apt-get|apt|apk|yum|dnf)\b[^|;&]*\b(?:install|add)\b|\bnpm (?:install|i) (?:-g|--global)\b|\bcurl\b.*\|\s*(?:ba)?sh\b"
HEAVY = r"playwright|cypress|selenium|e2e|\bdast\b|\bsast\b"


def minutes(value):
    """A GitLab duration ('15m', '1h 30m', '2 hours', bare seconds) in minutes; None if it is not one."""
    value = value.strip().strip("'\"").lower()
    if value.isdigit():
        return int(value) / 60
    parts = re.findall(r"(\d+(?:\.\d+)?)\s*(mo|[smhdwy])", value)
    return sum(float(n) * UNIT_MINUTES[unit] for n, unit in parts) if parts else None


def strip_comments(content):
    return "\n".join(re.sub(r"(^|\s)#.*", "", line) for line in content.splitlines())


def top_level_blocks(text):
    """Top-level key -> the text of its block."""
    blocks = {}
    for chunk in re.split(r"^(?=\S)", text, flags=re.M):
        key = re.match(r"([^\s:]+(?::[^\s:]+)*):", chunk)
        if key:
            blocks[key.group(1)] = chunk
    return blocks


def nested(body, key):
    """The lines indented under `key:` in a block."""
    match = re.search(rf"^([ \t]+){key}:[ \t]*\n((?:\1[ \t]+.*\n|\s*\n)*)", body + "\n", re.M)
    return match.group(2) if match else ""


def off_push_path(body):
    """True when no rule lets a push start the job: every rule is a schedule, manual or never."""
    rules = nested(body, "rules")
    if not rules.strip():
        return bool(re.search(r"^\s+when:\s*manual", body, re.M))
    indent = re.match(r"\s*", rules).group()
    items = re.split(rf"^{indent}- ", rules, flags=re.M)[1:]
    return all(re.search(r"CI_PIPELINE_SOURCE\s*==\s*['\"]schedule['\"]|when:\s*(?:manual|never)", item) for item in items)


def heap_over_budget(name, body, variables, max_workers, heap_limit):
    """Why this job's heap ceilings, summed over its processes, miss the budget, or None."""
    def cap(pattern, unit=lambda n, u: float(n)):
        found = re.findall(pattern, body) or re.findall(pattern, variables)
        return unit(*found[-1]) if found else None

    def count(pattern):
        # A count over max_workers is the worker check's finding; budget the heap for the cap it asks for.
        workers = re.search(pattern, body)
        return min(int(workers.group(1)), max_workers) if workers and workers.group(1).isdigit() else max_workers

    checks = []
    if re.search(NODE_STACK, body):
        processes = count(r"(?:--maxWorkers|max(?:Forks|Threads)|-w)[= ](\S+)") + 1 if re.search(r"\b(?:jest|vitest)\b", body) else 1
        checks.append(("--max-old-space-size", cap(r"--max-old-space-size=(\d+)()"), processes))
    if re.search(r"\bgo (?:test|build)\b", body):
        processes = count(r"(?<!\S)-p[= ](\S+)") if re.search(r"\bgo test\b", body) else 1
        checks.append(("GOMEMLIMIT", cap(r"GOMEMLIMIT[:=]\s*['\"]?(\d+(?:\.\d+)?)([KMG]?)i?B?\b", lambda n, u: float(n) * MIB[u.lower()]), processes))
    for flag, mb, processes in checks:
        if mb is None:
            return f"{name}: no {flag}"
        if mb * processes > heap_limit:
            return f"{name}: {flag} {mb:.0f} MB x {processes} processes"
    return None


def uncapped_runner(line, max_workers):
    """Why a test command on this line can exceed max_workers, or None."""
    def over(pattern, required):
        cap = re.search(pattern, line)
        if not cap:
            return required
        return not cap.group(1).isdigit() or not 1 <= int(cap.group(1)) <= max_workers

    if re.search(r"\b(?:jest|vitest)\b", line) and over(r"(?:--maxWorkers|max(?:Forks|Threads)|-w)[= ](\S+)", True):
        return f"jest/vitest needs --maxWorkers={max_workers}"
    if re.search(r"\bplaywright test\b", line) and over(r"--workers[= ](\S+)", True):
        return f"playwright needs --workers={max_workers} or fewer"
    if re.search(r"\bgo test\b", line) and over(r"(?<!\S)-p[= ](\S+)", True):
        return f"go test needs -p {max_workers}"
    # pytest is serial unless pytest-xdist's -n is passed, so a missing flag is already capped.
    if re.search(r"\bpytest\b", line) and over(r"(?:(?<!\S)-n|--numprocesses)[= ]?(\S+)", False):
        return f"pytest -n must be a number <= {max_workers}"
    return None


def audit_pipeline(content, ram_mb=2048, max_workers=2):
    """Check results for a pipeline: {"checks": {key: {"ok", "title", "detail"}}, "passed", "failed"}.

    "ok" is True, False, or None when the check does not apply to this pipeline.
    """
    text = strip_comments(content)
    blocks = top_level_blocks(text)
    default = blocks.get("default", "")
    workflow = blocks.get("workflow", "")
    jobs = {name: body for name, body in blocks.items() if re.search(r"^\s+(?:script|trigger):", body, re.M)}
    has_scripts = any(re.search(r"^\s+script:", body, re.M) for body in jobs.values())
    heap_limit = ram_mb * HEAP_FRACTION
    checks = {}

    def check(key, title, ok, passed, fix):
        checks[key] = {"ok": ok, "title": title, "detail": "Not applicable to this pipeline." if ok is None else passed if ok else fix}

    dedup = "CI_OPEN_MERGE_REQUESTS" in workflow and re.search(r"when:\s*never", workflow)
    # Only merge request pipelines or only branch pipelines, with no catch-all rule that admits the other.
    catch_all = re.search(r"^\s*- when:\s*(?:always|on_success|manual|delayed)", workflow, re.M)
    one_source = ("merge_request_event" in workflow) != ("CI_COMMIT_BRANCH" in workflow) and not catch_all
    check("one_pipeline_per_push", "One pipeline per push (workflow: rules)", bool(dedup or one_source),
          "workflow: rules cannot start both a branch and a merge request pipeline for one push.",
          "Add workflow: rules with `- if: $CI_COMMIT_BRANCH && $CI_OPEN_MERGE_REQUESTS` / `when: never`.")

    check("interruptible", "Stale pipelines cancel (default: interruptible: true)",
          bool(re.search(r"^\s+interruptible:\s*true\s*$", default, re.M | re.I)),
          "default: interruptible: true is set.",
          "Set `interruptible: true` under `default:`, and `interruptible: false` on deploy and publish jobs.")

    timeout = re.search(r"^\s+timeout:(.*)$", default, re.M)
    timeout_text = timeout.group(1).strip() if timeout else "none"
    timeout_min = minutes(timeout_text)
    check("tight_timeout", f"Default job timeout <= {DEFAULT_TIMEOUT_MIN}m",
          timeout_min is not None and timeout_min <= DEFAULT_TIMEOUT_MIN,
          f"default: timeout is {timeout_text}.",
          f"Set `timeout: {DEFAULT_TIMEOUT_MIN}m` under `default:` (found: {timeout_text}).")

    expiries = [minutes(value) for value in re.findall(r"^\s+expire_in:(.*)$", text, re.M)]
    artifact_blocks = len(re.findall(r"^\s+artifacts:", text, re.M))
    check("artifact_expiration", "Every artifacts: block expires within 1 day",
          None if not artifact_blocks else len(expiries) >= artifact_blocks and all(e is not None and e <= ARTIFACT_MAX_MIN for e in expiries),
          f"All {artifact_blocks} artifacts: blocks set expire_in <= 1 day.",
          "Give every artifacts: block an `expire_in` of 1 day or less (`1 hour` for dependencies).")

    depth = re.search(r"^\s+GIT_DEPTH:\s*['\"]?(\d+)['\"]?\s*$", blocks.get("variables", ""), re.M)
    check("shallow_clone", f"Shallow git clone (GIT_DEPTH 1-{GIT_DEPTH_MAX})",
          None if not has_scripts else bool(depth) and 1 <= int(depth.group(1)) <= GIT_DEPTH_MAX,
          f"GIT_DEPTH is {depth.group(1) if depth else 'unset'}.",
          f'Set `GIT_DEPTH: "{GIT_DEPTH_MAX}"` under `variables:` (0 means a full clone).')

    runner_lines = [line.strip() for line in text.splitlines() if re.search(TEST_RUNNER, line)]
    uncapped = [reason for reason in (uncapped_runner(line, max_workers) for line in runner_lines) if reason]
    check("worker_capping", f"Test workers capped to runner vCPUs (<= {max_workers})",
          None if not runner_lines else not uncapped,
          f"All {len(runner_lines)} test commands cap their workers.",
          f"Uncapped test commands: {'; '.join(sorted(set(uncapped)))}.")

    variables = blocks.get("variables", "")
    heap_jobs = [name for name, body in jobs.items() if re.search(NODE_STACK + r"|\bgo (?:test|build)\b", body)]
    over = [why for why in (heap_over_budget(name, jobs[name], variables, max_workers, heap_limit) for name in heap_jobs) if why]
    check("heap_capping", f"Runtime heap ceiling <= {HEAP_FRACTION:.0%} of runner RAM ({ram_mb} MB)",
          None if not heap_jobs else not over,
          f"Every job's heap ceilings, summed over its processes, fit in {heap_limit:.0f} MB.",
          f"Over the {heap_limit:.0f} MB budget or uncapped: {'; '.join(over)}. Divide the ceiling by the processes the job runs.")

    installs = [line.strip() for line in text.splitlines() if re.search(INSTALL, line)]
    check("install_once", "Dependencies installed in one job only",
          None if not installs else len(installs) == 1,
          "One install command; other jobs receive dependencies through needs:.",
          f"{len(installs)} install commands found. Keep one `install` job, pass its output as artifacts, and use `needs: [install]`.")

    tool_installs = [line.strip() for line in text.splitlines() if re.search(TOOL_INSTALL, line)]
    check("prebuilt_ci_image", "No tool installs at job time (prebuilt CI image)",
          None if not has_scripts else not tool_installs,
          "No apt/apk/yum/npm -g/curl|sh installs in any script.",
          f"Move these into Dockerfile.ci: {'; '.join(tool_installs)}")

    gated = [name for name, body in jobs.items() if re.search(TEST_RUNNER + r"|^\s+trigger:", body, re.M) and not off_push_path(body)]
    ungated = [name for name in gated if not re.search(r"^\s+(?:- )?changes:", jobs[name], re.M)]
    check("skip_unchanged", "Test or trigger jobs skip untouched code (rules: changes:)",
          None if not gated else not ungated,
          "Every test or trigger job on the push path is gated by rules: changes:.",
          f"These run on every push whatever changed: {', '.join(ungated)}. Add `rules: changes:` to each.")

    heavy = [name for name, body in jobs.items() if re.search(HEAVY, body, re.I)]
    on_push = [name for name in heavy if not off_push_path(jobs[name])]
    check("heavy_off_push_path", "E2E and security scans off the push path (schedule or manual)",
          None if not heavy else not on_push,
          f"Heavy jobs run on a schedule or manually: {', '.join(heavy)}.",
          f"These run on every push: {', '.join(on_push)}. Gate them with a schedule rule or `when: manual`.")

    passed = sum(1 for c in checks.values() if c["ok"])
    failed = sum(1 for c in checks.values() if c["ok"] is False)
    return {
        "jobs": len(jobs),
        "score": round(passed / (passed + failed) * 100) if passed + failed else 0,
        "passed": passed,
        "failed": failed,
        "checks": checks,
    }


def print_report(res, filename):
    print("=" * 68)
    print(f"  GITLAB CI PIPELINE AUDIT REPORT: {filename}")
    print(f"  LEAN SCORE: {res['score']} / 100  ({res['passed']}/{res['passed'] + res['failed']} applicable checks passed)")
    print("=" * 68)
    print()
    for c in res["checks"].values():
        print(f"[{ {True: 'PASS', False: 'FAIL', None: ' N/A'}[c['ok']] }] {c['title']}")
        print(f"       {c['detail']}")
    print()
    print("-" * 68)
    if res["score"] >= 85:
        print("Verdict: EXCELLENT. Pipeline follows lean best practices.")
    elif res["score"] >= 60:
        print("Verdict: MODERATE. Apply recommended remediation to prevent OOMs & save runner minutes.")
    else:
        print("Verdict: HIGH RISK. Pipeline is prone to OOM kills (137) and excessive runner billing.")
    print("These are text matches on one file: read the YAML before acting on a result.")
    print("-" * 68)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check a .gitlab-ci.yml against the lean checklist.")
    parser.add_argument("path", nargs="?", default=".gitlab-ci.yml", help="pipeline file (default: .gitlab-ci.yml)")
    parser.add_argument("--ram", type=int, default=2048, help="runner container RAM in MB (default: 2048)")
    parser.add_argument("--max-workers", type=int, default=2, help="runner container vCPUs (default: 2)")
    args = parser.parse_args(argv)

    try:
        with open(args.path, "r", encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError) as error:
        print(f"Error: cannot read {args.path}: {error}", file=sys.stderr)
        return 2

    result = audit_pipeline(content, args.ram, args.max_workers)
    if not result["jobs"]:
        print(f"Error: no jobs with script: or trigger: in {args.path}. If it only has include:, audit the included files.", file=sys.stderr)
        return 2
    print_report(result, args.path)
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
