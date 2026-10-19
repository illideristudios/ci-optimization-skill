# Codex Instructions: GitLab CI Pipeline Standards

When generating or modifying `.gitlab-ci.yml`:
1. Check runner memory budget before adding workers or test suites. Run `python3 .agents/skills/gitlab-ci-optimizer/scripts/simulate_budget.py`.
2. Cap workers to runner vCPUs (max 2).
3. Set runtime heap ceilings (`NODE_OPTIONS`, `GOMEMLIMIT`, `MALLOC_ARENA_MAX`).
4. Enforce install-once: one `install` job fills a lockfile-keyed cache (`policy: pull-push`), consumers declare `needs: [install]` and pull it (`policy: pull`). Dependencies are never artifacts; any artifact that must exist sets `expire_in: 1 hour`.

   ```
   A(j) := job j uploads artifacts to GitLab
   M(j) := a later job or a person needs j's exact output and cannot rebuild it from the cache
   V(j) := j's output is a dependency/vendor folder (node_modules/, .venv/, .nuget/, obj/)
   E(j) := j's artifacts set expire_in: 1 hour

   1.  A(j) → M(j)       [Zach, 2026-10-19: "artifacts THAT MUST BE THERE expire after 1 hour"]
   2.  V(j) → ¬M(j)      [the install job's lockfile-keyed cache carries dependencies to every consumer]
   3.  A(j) → E(j)       [Zach, 2026-10-19]
   4.  V(j) → ¬A(j)      [2, then the contrapositive of 1: ¬M(j) → ¬A(j)]
   ∴   install jobs upload nothing, and any artifact that does exist expires in 1 hour
   ```
5. Enforce `workflow: rules` to eliminate duplicate merge request pipelines.
6. Verify pipeline with `python3 .agents/skills/gitlab-ci-optimizer/scripts/audit_pipeline.py .gitlab-ci.yml`.
