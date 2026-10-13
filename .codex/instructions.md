# Codex Instructions: GitLab CI Pipeline Standards

When generating or modifying `.gitlab-ci.yml`:
1. Check runner memory budget before adding workers or test suites. Run `python3 .agents/skills/gitlab-ci-optimizer/scripts/simulate_budget.py`.
2. Cap workers to runner vCPUs (max 2).
3. Set runtime heap ceilings (`NODE_OPTIONS`, `GOMEMLIMIT`, `MALLOC_ARENA_MAX`).
4. Enforce install-once pattern with `needs: [install]` and `expire_in: 1 hour`.
5. Enforce `workflow: rules` to eliminate duplicate merge request pipelines.
6. Verify pipeline with `python3 .agents/skills/gitlab-ci-optimizer/scripts/audit_pipeline.py .gitlab-ci.yml`.
