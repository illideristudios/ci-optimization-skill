# Agent Instructions

## GitLab CI/CD pipelines (`.gitlab-ci.yml`, `Dockerfile.ci`)

Before creating, editing, or reviewing a GitLab CI pipeline, read `.agents/skills/gitlab-ci-optimizer/SKILL.md` and follow it. It is the single source for the rules; nothing is restated here.

- Audit: `python3 .agents/skills/gitlab-ci-optimizer/scripts/audit_pipeline.py .gitlab-ci.yml`
- Budget estimate (`/simulate-budget`): `python3 .agents/skills/gitlab-ci-optimizer/scripts/simulate_budget.py --help` lists the flags.
