#!/usr/bin/env python3
"""
GitLab CI Runner Memory & Concurrency Budget Simulator
Calculates peak memory, wall-clock time, and runner saturation based on
the formulas from the Gauntlet GitLab Primer.
"""

import argparse
import sys

STACK_DEFAULTS = {
    "node": {
        "label": "Node.js (Jest/Vitest)",
        "base_mb": 250,
        "per_worker_mb": 300,
        "suite_cpu_mins": 24,
        "build_mins": 2.5,
        "install_mins": 1.5,
        "heap_flag": lambda ram: f'NODE_OPTIONS="--max-old-space-size={int(ram * 0.75)}"'
    },
    "python": {
        "label": "Python (Pytest/uv)",
        "base_mb": 200,
        "per_worker_mb": 350,
        "suite_cpu_mins": 20,
        "build_mins": 1.0,
        "install_mins": 1.0,
        "heap_flag": lambda ram: 'MALLOC_ARENA_MAX="2"'
    },
    "go": {
        "label": "Go (go test)",
        "base_mb": 300,
        "per_worker_mb": 450,
        "suite_cpu_mins": 16,
        "build_mins": 2.0,
        "install_mins": 1.2,
        "heap_flag": lambda ram: f'GOMEMLIMIT="{int(ram * 0.75)}MiB"'
    }
}


def simulate(stack_name, ram_mb, vcpus, runner_slots, workers, shards, prebuilt=True, install_once=True):
    stack = STACK_DEFAULTS.get(stack_name.lower())
    if not stack:
        print(f"Unknown stack '{stack_name}'. Available: node, python, go", file=sys.stderr)
        sys.exit(1)

    # 1. RAM Calculation
    # Uncapped workers accumulate memory; more shards = fewer files per worker
    effective_per_worker = stack["per_worker_mb"]
    peak_ram_mb = stack["base_mb"] + (workers * effective_per_worker)
    ram_usage_pct = (peak_ram_mb / ram_mb) * 100

    if peak_ram_mb > ram_mb:
        oom_status = "OOM_KILL (Exit 137)"
        status_color = "\033[31m"
    elif peak_ram_mb > (ram_mb * 0.85):
        oom_status = "TIGHT (Risk of OOM on heavy test suites)"
        status_color = "\033[33m"
    else:
        oom_status = "FITS (Safe buffer for OS & IO)"
        status_color = "\033[32m"

    # 2. Concurrency and Thrashing
    effective_workers = min(workers, vcpus)
    cpu_thrash_multiplier = 1.15 if workers > vcpus else 1.0

    # 3. Setup taxes
    setup_tax = 0.3 if prebuilt else 1.2
    install_tax = 0.3 if install_once else stack["install_mins"]

    # 4. Job timings
    test_work_mins = (stack["suite_cpu_mins"] / (effective_workers * shards)) * cpu_thrash_multiplier
    test_job_mins = setup_tax + install_tax + test_work_mins
    lint_job_mins = setup_tax + install_tax + 0.8
    build_job_mins = setup_tax + install_tax + stack["build_mins"]

    # 5. Wall-clock time simulation onto runner slots
    jobs = []
    if install_once:
        jobs.append({"name": "install", "time": setup_tax + stack["install_mins"]})
    jobs.append({"name": "lint", "time": lint_job_mins})
    for s in range(shards):
        jobs.append({"name": f"test {s+1}/{shards}", "time": test_job_mins})
    jobs.append({"name": "build", "time": build_job_mins})

    # Earliest available slot assignment (LPT: Longest Processing Time first)
    slots = [0.0] * runner_slots
    if install_once:
        # Install runs first, then parallel work follows
        install_time = jobs[0]["time"]
        parallel_jobs = sorted(jobs[1:], key=lambda x: x["time"], reverse=True)
        for j in parallel_jobs:
            min_slot_idx = slots.index(min(slots))
            slots[min_slot_idx] += j["time"]
        wall_clock = install_time + max(slots)
    else:
        sorted_jobs = sorted(jobs, key=lambda x: x["time"], reverse=True)
        for j in sorted_jobs:
            min_slot_idx = slots.index(min(slots))
            slots[min_slot_idx] += j["time"]
        wall_clock = max(slots)

    total_compute_mins = sum(j["time"] for j in jobs)

    # 6. Advice / Recommendations
    recommendations = []
    if workers > vcpus:
        recommendations.append(
            f"Workers ({workers}) exceed runner vCPUs ({vcpus}): Spawns extra processes without speeding up tests, wasting RAM. Cap workers to {vcpus}."
        )
    if shards > runner_slots:
        recommendations.append(
            f"Test shards ({shards}) exceed concurrent runner slots ({runner_slots}): Shards will queue in waves. Reduce shards to <= {runner_slots}."
        )
    if not prebuilt:
        recommendations.append("Bake tools into Dockerfile.ci pushed to $CI_REGISTRY_IMAGE/ci:latest to save ~1m per job.")
    if not install_once:
        recommendations.append("Use a single install job that passes dependencies via artifacts (expire_in: 1 hour) with needs: [install].")
    if peak_ram_mb > ram_mb:
        optimal_workers = max(1, int((ram_mb * 0.75 - stack["base_mb"]) / stack["per_worker_mb"]))
        recommendations.append(f"To avoid Exit 137 OOM on {ram_mb}MB RAM, reduce workers to {optimal_workers}.")

    print("=" * 64)
    print(f"  RUNNER SIMULATION: {stack['label']}")
    print(f"  Runner Specs: {ram_mb} MB RAM, {vcpus} vCPU, {runner_slots} Concurrent Slots")
    print("=" * 64)
    print(f"Memory Status:      {status_color}{oom_status}\033[0m")
    print(f"Peak RAM Required:  {peak_ram_mb:.0f} MB ({ram_usage_pct:.1f}% of runner capacity)")
    print(f"Recommended Cap:    {stack['heap_flag'](ram_mb)}")
    print(f"Total Wall-Clock:   {wall_clock:.1f} minutes")
    print(f"Total Compute:      {total_compute_mins:.1f} runner-minutes")
    print("-" * 64)
    if recommendations:
        print("ACTIONABLE RECOMMENDATIONS:")
        for r in recommendations:
            print(f"  • {r}")
    else:
        print("Configuration is optimal and lean!")
    print("=" * 64)


def main():
    parser = argparse.ArgumentParser(description="GitLab CI Runner Memory & Concurrency Budget Simulator")
    parser.add_argument("--stack", default="node", choices=["node", "python", "go"], help="Tech stack (node, python, go)")
    parser.add_argument("--ram", type=int, default=2048, help="Runner container RAM in MB (default: 2048)")
    parser.add_argument("--vcpus", type=int, default=2, help="Runner container vCPUs (default: 2)")
    parser.add_argument("--slots", type=int, default=2, help="Concurrent runner slots available (default: 2)")
    parser.add_argument("--workers", type=int, default=2, help="Test workers per job (default: 2)")
    parser.add_argument("--shards", type=int, default=1, help="Test parallel shards (default: 1)")
    parser.add_argument("--no-prebuilt", action="store_true", help="Simulate without prebuilt CI image")
    parser.add_argument("--no-install-once", action="store_true", help="Simulate without install-once artifact passing")

    args = parser.parse_args()
    simulate(
        stack_name=args.stack,
        ram_mb=args.ram,
        vcpus=args.vcpus,
        runner_slots=args.slots,
        workers=args.workers,
        shards=args.shards,
        prebuilt=not args.no_prebuilt,
        install_once=not args.no_install_once
    )


if __name__ == "__main__":
    main()
