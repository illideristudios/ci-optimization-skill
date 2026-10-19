#!/usr/bin/env python3
"""
GitLab CI Runner Memory & Concurrency Budget Simulator
Estimates peak memory, wall-clock time, and runner saturation for one
pipeline from the worker-cap formula in references/memory-and-oom.md.

Exit 0 when the job fits in runner RAM, 1 when it would be OOM killed.
"""

import argparse
import sys

# Illustrative figures for a mid-sized suite, not measurements: replace them
# with numbers from a real job log before trusting the absolute values.
STACK_DEFAULTS = {
    "node": {
        "label": "Node.js (Jest/Vitest)",
        "base_mb": 250,
        "per_worker_mb": 300,
        "suite_cpu_mins": 24,
        "build_mins": 2.5,
        "install_mins": 1.5,
        # --max-old-space-size applies to the parent and to each worker process
        "heap_flag": lambda heap_mb, workers: f'NODE_OPTIONS="--max-old-space-size={heap_mb // (workers + 1)}"'
    },
    "python": {
        "label": "Python (Pytest/uv)",
        "base_mb": 200,
        "per_worker_mb": 350,
        "suite_cpu_mins": 20,
        "build_mins": 1.0,
        "install_mins": 1.0,
        "heap_flag": lambda heap_mb, workers: 'none (CPython has no heap cap; MALLOC_ARENA_MAX="2" limits glibc fragmentation)'
    },
    "go": {
        "label": "Go (go test)",
        "base_mb": 300,
        "per_worker_mb": 450,
        "suite_cpu_mins": 16,
        "build_mins": 2.0,
        "install_mins": 1.2,
        # GOMEMLIMIT is per process and `go test -p N` runs N test binaries
        "heap_flag": lambda heap_mb, workers: f'GOMEMLIMIT="{heap_mb // workers}MiB" GOMAXPROCS="{workers}"'
    }
}

HEAP_FRACTION = 0.75        # share of runner RAM the runtime may use; the rest is OS, git and I/O
DEFAULT_TIMEOUT_MINS = 15   # the default: timeout the skill prescribes


def simulate(stack_name, ram_mb, vcpus, runner_slots, workers, shards, prebuilt=True, install_once=True):
    """Budget for one pipeline as a dict: status, peak_ram_mb, heap_flag, wall_clock, total_compute, recommendations."""
    stack = STACK_DEFAULTS[stack_name]
    budget_mb = int(ram_mb * HEAP_FRACTION)

    # 1. RAM per test job. Shards split the files, not the per-worker footprint.
    peak_ram_mb = stack["base_mb"] + workers * stack["per_worker_mb"]
    if peak_ram_mb > ram_mb:
        status = "OOM_KILL"
    elif peak_ram_mb > budget_mb:
        status = "TIGHT"
    else:
        status = "FITS"

    # 2. Concurrency and Thrashing
    effective_workers = min(workers, vcpus)
    cpu_thrash_multiplier = 1.15 if workers > vcpus else 1.0

    # 3. Setup taxes
    setup_tax = 0.3 if prebuilt else 1.2
    install_tax = 0.3 if install_once else stack["install_mins"]

    # 4. Job timings
    test_work_mins = (stack["suite_cpu_mins"] / (effective_workers * shards)) * cpu_thrash_multiplier
    test_job_mins = setup_tax + install_tax + test_work_mins
    parallel_jobs = [setup_tax + install_tax + 0.8, setup_tax + install_tax + stack["build_mins"]] + [test_job_mins] * shards
    install_mins = setup_tax + stack["install_mins"] if install_once else 0.0

    # 5. Wall-clock: install runs alone, then the rest go longest-first onto the
    # least-loaded slot. GitLab starts jobs in creation order, so this is a best case.
    slots = [0.0] * runner_slots
    for job_mins in sorted(parallel_jobs, reverse=True):
        slots[slots.index(min(slots))] += job_mins

    # 6. Advice / Recommendations
    recommendations = []
    workers_that_fit = (budget_mb - stack["base_mb"]) // stack["per_worker_mb"]
    target_workers = min(vcpus, workers_that_fit)
    cap_workers = max(1, min(workers, target_workers))
    if workers_that_fit < 1:
        needed_mb = int((stack["base_mb"] + stack["per_worker_mb"]) / HEAP_FRACTION) + 1
        recommendations.append(f"Runner too small: one worker needs about {needed_mb} MB of runner RAM. Use a larger runner.")
    elif workers > target_workers:
        recommendations.append(
            f"Reduce workers from {workers} to {target_workers}: the runner has {vcpus} vCPUs and {workers_that_fit} workers fit in {budget_mb} MB."
        )
    if shards > runner_slots:
        recommendations.append(
            f"Test shards ({shards}) exceed concurrent runner slots ({runner_slots}): Shards will queue in waves. Reduce shards to <= {runner_slots}."
        )
    if test_job_mins > DEFAULT_TIMEOUT_MINS:
        recommendations.append(
            f"Each test job runs about {test_job_mins:.1f} minutes, past the {DEFAULT_TIMEOUT_MINS}m default timeout. Add shards (parallel: N)."
        )
    if not prebuilt:
        recommendations.append("Bake tools into Dockerfile.ci pushed to $CI_REGISTRY_IMAGE/ci:latest to save setup time in every job.")
    if not install_once:
        recommendations.append("Use a single install job that fills a lockfile-keyed cache (policy: pull-push); jobs that need: [install] pull it (policy: pull). Never pass dependencies as artifacts.")

    return {
        "label": stack["label"],
        "status": status,
        "peak_ram_mb": peak_ram_mb,
        "ram_usage_pct": peak_ram_mb / ram_mb * 100,
        "cap_workers": cap_workers,
        "heap_flag": stack["heap_flag"](budget_mb, cap_workers),
        "wall_clock": install_mins + max(slots),
        "total_compute": install_mins + sum(parallel_jobs),
        "suite_cpu_mins": stack["suite_cpu_mins"],
        "recommendations": recommendations,
    }


STATUS_TEXT = {
    "OOM_KILL": ("\033[31m", "OOM_KILL (Exit 137)"),
    "TIGHT": ("\033[33m", "TIGHT (over 75% of runner RAM)"),
    "FITS": ("\033[32m", "FITS (within 75% of runner RAM)"),
}


def print_report(res, ram_mb, vcpus, runner_slots):
    color, status = STATUS_TEXT[res["status"]]
    if sys.stdout.isatty():
        status = f"{color}{status}\033[0m"
    print("=" * 64)
    print(f"  RUNNER SIMULATION: {res['label']}")
    print(f"  Runner Specs: {ram_mb} MB RAM, {vcpus} vCPU, {runner_slots} Concurrent Slots")
    print("=" * 64)
    print(f"Memory Status:      {status}")
    print(f"Peak RAM per job:   {res['peak_ram_mb']} MB ({res['ram_usage_pct']:.1f}% of runner RAM)")
    print(f"Cap at {res['cap_workers']} worker(s):  {res['heap_flag']}")
    print(f"Est. Wall-Clock:    {res['wall_clock']:.1f} minutes")
    print(f"Est. Compute:       {res['total_compute']:.1f} runner-minutes")
    print("-" * 64)
    if res["recommendations"]:
        print("RECOMMENDATIONS:")
        for r in res["recommendations"]:
            print(f"  - {r}")
    else:
        print("No recommendations.")
    print("-" * 64)
    print(f"Assumes an illustrative {res['suite_cpu_mins']} CPU-minute suite and per-worker memory;")
    print("jobs are assumed not to share RAM. Check against a real job log.")
    print("=" * 64)


def positive_int(text):
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description="GitLab CI Runner Memory & Concurrency Budget Simulator")
    parser.add_argument("--stack", default="node", choices=list(STACK_DEFAULTS), help="Tech stack (default: node)")
    parser.add_argument("--ram", type=positive_int, default=2048, help="Runner container RAM in MB (default: 2048)")
    parser.add_argument("--vcpus", type=positive_int, default=2, help="Runner container vCPUs (default: 2)")
    parser.add_argument("--slots", type=positive_int, default=2, help="Concurrent runner slots available (default: 2)")
    parser.add_argument("--workers", type=positive_int, default=2, help="Test workers per job (default: 2)")
    parser.add_argument("--shards", type=positive_int, default=1, help="Test parallel shards (default: 1)")
    parser.add_argument("--no-prebuilt", action="store_true", help="Simulate without prebuilt CI image")
    parser.add_argument("--no-install-once", action="store_true", help="Simulate without the install-once dependency cache")

    args = parser.parse_args(argv)
    result = simulate(
        stack_name=args.stack,
        ram_mb=args.ram,
        vcpus=args.vcpus,
        runner_slots=args.slots,
        workers=args.workers,
        shards=args.shards,
        prebuilt=not args.no_prebuilt,
        install_once=not args.no_install_once
    )
    print_report(result, args.ram, args.vcpus, args.slots)
    return 1 if result["status"] == "OOM_KILL" else 0


if __name__ == "__main__":
    sys.exit(main())
