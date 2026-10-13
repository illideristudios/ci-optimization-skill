# Runner Memory & OOM Shielding Reference

## 1. Anatomy of an OOM Failure

When running inside Docker-based or Kubernetes GitLab Runners, jobs are subject to strict container cgroup memory limits (often 1GB to 4GB on shared or small dedicated runners).

### Symptoms
- **Exit code 137**: `128 + 9 (SIGKILL)`. The Linux kernel OOM Killer terminated the process because the container exceeded its `memory.max` or `memory.limit_in_bytes`.
- **Bare `Killed` in job log**: The process was reaped mid-execution without a stack trace.
- **Node.js**: `FATAL ERROR: Ineffective mark-compacts near heap limit Allocation failed - JavaScript heap out of memory`.
- **Python**: `MemoryError` or silent child process crash under `pytest-xdist`.
- **JVM**: `java.lang.OutOfMemoryError: Java heap space` or exit 137 when native/container limits are breached.

---

## 2. The Host CPU & Concurrency Trap

### Why Uncapped Tools Blow Up Memory
Inside a container allocated 2 vCPUs on a 16-core or 32-core host node, tools like Jest, Vitest, Pytest, Go, Cargo, or Make frequently query CPU count via `os.cpus()`, `nproc`, or `sysconf(_SC_NPROCESSORS_ONLN)`.

- Default behavior: Spawns **N - 1 workers** (e.g. 15-31 worker processes).
- **Result**:
  1. Extreme CPU thrashing and context switching on the 2 assigned vCPUs.
  2. Each worker instantiates a full language runtime heap (e.g., 300MB - 600MB each).
  3. 15 workers * 350MB = **5.25 GB**, immediately exceeding a 2GB or 4GB runner limit.

### Worker Cap Formula
```text
Workers = min(runner_vcpus, floor((runner_ram_mb - base_overhead_mb) / per_worker_ram_mb))
```
*Rule of Thumb*: Never set workers higher than the runner container's vCPU allocation (usually `2`).

---

## 3. Stack-by-Stack Memory Ceilings

Aim for the total runtime heap ceiling to occupy **~75% of runner container RAM**, reserving the remaining 25% for the OS kernel, glibc, Git, runner sidecars, and file system buffers.

### Node.js / TypeScript
1. **Global Heap Cap**:
   ```yaml
   variables:
     # For a 2GB (2048MB) runner: 75% = 1536MB
     NODE_OPTIONS: "--max-old-space-size=1536"
   ```
2. **Multi-Worker Test Runners (Jest / Vitest)**:
   When running multiple workers, Node's `--max-old-space-size` applies *per worker process*. Therefore:
   ```yaml
   variables:
     # Formula: (Runner_RAM * 0.75) / (workers + 1)
     # On 2048MB runner with 2 workers: 1536 / 3 = 512MB
     NODE_OPTIONS: "--max-old-space-size=512"
   ```
3. **Jest Worker Recycling**:
   Jest workers accumulate memory across test files without garbage collecting aggressively. Cap workers and recycle idle workers:
   ```bash
   npx jest --maxWorkers=2 --workerIdleMemoryLimit=512MB
   ```
4. **Vitest**:
   ```bash
   npx vitest run --pool=forks --poolOptions.forks.maxForks=2 --poolOptions.forks.minForks=1
   ```

### Python
Python processes under `pytest-xdist` duplicate memory per fork/process.
1. **Cap Pytest Workers**:
   ```bash
   uv run pytest -n 2
   ```
2. **Prevent Buffer Inflation**:
   ```yaml
   variables:
     PYTHONUNBUFFERED: "1"
     MALLOC_ARENA_MAX: "2"  # Curtails glibc memory fragmentation in multi-threaded/forked workloads
   ```

### Go
Go 1.19+ introduced a soft memory limit that triggers GC before the kernel OOM killer steps in.
1. **Soft Heap Limit**:
   ```yaml
   variables:
     # Set to 75-80% of runner RAM
     GOMEMLIMIT: "1500MiB"
   ```
2. **Test Parallelism**:
   ```bash
   go test -p 2 ./...
   ```

### Java / JVM
Modern JVMs respect container cgroups automatically with proper flags.
1. **Container RAM Percentage**:
   ```yaml
   variables:
     JAVA_TOOL_OPTIONS: "-XX:+UseContainerSupport -XX:MaxRAMPercentage=75.0 -XX:InitialRAMPercentage=50.0"
   ```
2. **Gradle Daemon**:
   Disable background daemons that stay resident and consume memory:
   ```yaml
   variables:
     GRADLE_OPTS: "-Dorg.gradle.daemon=false -Dorg.gradle.jvmargs=\"-Xmx1536m -XX:MaxMetaspaceSize=256m\""
   ```

### Rust / Cargo
Cargo and `rustc` with LTO (Link Time Optimization) or debug symbols can demand huge RAM.
1. **Limit Codegen Units & Linker RAM**:
   ```yaml
   variables:
     CARGO_BUILD_JOBS: "2"
     RUSTFLAGS: "-C codegen-units=2 -C debuginfo=0"
   ```

---

## 4. Troubleshooting Diagnostic Matrix

| Observation | Root Cause | Immediate Remediation |
| :--- | :--- | :--- |
| `Job failed: exit code 137` during test suite | Runner container OOM killed | Reduce test workers to 2 (`--maxWorkers=2`, `-n 2`), set heap ceiling (`NODE_OPTIONS`, `GOMEMLIMIT`). |
| Exit 137 during `docker build` | Docker daemon or build step exceeded RAM | Avoid multi-stage builds on unprivileged small runners; prebuild tools in `Dockerfile.ci`. |
| Memory climbs continuously until job crash | Memory leak across test files or uncapped worker cache | Add Jest `--workerIdleMemoryLimit=512MB`, or shard tests with `parallel: N`. |
| Job runs 3x slower in CI than laptop despite high CPU usage | Worker count matches host CPU (e.g. 16) causing thrashing on 2 vCPUs | Explicitly pin workers to 2. |
