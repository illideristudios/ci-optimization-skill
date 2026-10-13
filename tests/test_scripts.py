"""Tests for the auditor and the budget simulator. Run: python3 -m unittest discover -s tests"""

import contextlib
import io
import sys
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent / "skills" / "gitlab-ci-optimizer"
sys.path.insert(0, str(SKILL / "scripts"))

import audit_pipeline as audit  # noqa: E402
import simulate_budget as sim  # noqa: E402

NODE = (SKILL / "examples" / "node.gitlab-ci.yml").read_text()

# check key -> (text in the node example, a replacement that breaks that rule)
MUTATIONS = {
    "one_pipeline_per_push": ("    - if: $CI_COMMIT_BRANCH && $CI_OPEN_MERGE_REQUESTS\n      when: never\n", ""),
    "interruptible": ("  interruptible: true", "  interruptible: false  # was interruptible: true"),
    "tight_timeout": ("timeout: 15m", "timeout: 2h"),
    "artifact_expiration": ("expire_in: 1 hour", "expire_in: 30 days"),
    "shallow_clone": ('GIT_DEPTH: "20"', 'GIT_DEPTH: "0"'),
    "worker_capping": ("--maxWorkers=2", "--maxWorkers=40"),
    "heap_capping": ("--max-old-space-size=1536", "--max-old-space-size=8192"),
    "install_once": ("- npm run lint", "- npm ci && npm run lint"),
    "prebuilt_ci_image": ("- npm run build", "- apt-get install -y jq && npm run build"),
    "heavy_off_push_path": ('    - if: $CI_PIPELINE_SOURCE == "schedule"\n    - when: manual\n      allow_failure: true', "    - if: $CI_COMMIT_BRANCH"),
}

# Every keyword sits in a comment; the one real job breaks most of the rules.
JUNK = """\
# workflow:
#   rules: CI_OPEN_MERGE_REQUESTS when: never
# interruptible: true   timeout: 2h   expire_in: 1 hour   GIT_DEPTH: "20"
# --maxWorkers=2  NODE_OPTIONS: "--max-old-space-size=512"  Dockerfile.ci  needs: [install]  changes:  when: manual
test:
  image: ubuntu:latest
  script:
    - apt-get update && apt-get install -y nodejs npm
    - npm install
    - npx jest
    - npx playwright test
"""


def failed(content, **kwargs):
    return {key for key, check in audit.audit_pipeline(content, **kwargs)["checks"].items() if check["ok"] is False}


def run(main, *argv):
    """Exit code of a CLI main(), with its output swallowed."""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            return main(list(argv))
        except SystemExit as exit_:
            return exit_.code


class AuditTest(unittest.TestCase):
    def test_shipped_examples_pass_every_applicable_check(self):
        for path in sorted((SKILL / "examples").glob("*.yml")):
            with self.subTest(path.name):
                self.assertEqual(failed(path.read_text()), set())

    def test_each_rule_fails_when_the_node_example_breaks_it(self):
        for key, (old, new) in MUTATIONS.items():
            with self.subTest(key):
                self.assertIn(old, NODE)
                self.assertEqual(failed(NODE.replace(old, new)), {key})

    def test_changes_on_the_image_job_alone_does_not_count(self):
        head, sep, tail = NODE.partition("\ninstall:\n")
        self.assertEqual(failed(head + sep + tail.replace("changes:", "exists:")), {"skip_unchanged"})

    def test_keywords_in_comments_do_not_pass(self):
        self.assertEqual(
            failed(JUNK),
            {"one_pipeline_per_push", "interruptible", "tight_timeout", "shallow_clone", "worker_capping",
             "heap_capping", "prebuilt_ci_image", "skip_unchanged", "heavy_off_push_path"},
        )

    def test_yaml_form_of_node_options_is_a_heap_cap(self):
        self.assertIn(" --workerIdleMemoryLimit=512MB", NODE)
        self.assertEqual(failed(NODE.replace(" --workerIdleMemoryLimit=512MB", "")), set())

    def test_thresholds_follow_the_runner_size(self):
        self.assertEqual(failed(NODE, ram_mb=1024), {"heap_capping"})
        self.assertEqual(failed(NODE.replace("--maxWorkers=2", "--maxWorkers=4"), max_workers=4), set())

    def test_durations(self):
        for text, minutes in [("15m", 15), ("1h 30m", 90), ("2 hours", 120), ("30 days", 43200), ("3600", 60), ("never", None)]:
            with self.subTest(text):
                self.assertEqual(audit.minutes(text), minutes)

    def test_exit_codes(self):
        self.assertEqual(run(audit.main, str(SKILL / "examples" / "node.gitlab-ci.yml")), 0)
        self.assertEqual(run(audit.main, str(SKILL.parent.parent / "LICENSE")), 2)  # not a pipeline
        self.assertEqual(run(audit.main, str(SKILL / "no-such-file.yml")), 2)
        self.assertEqual(run(audit.main, str(SKILL)), 2)  # a directory


class SimulateTest(unittest.TestCase):
    def simulate(self, **overrides):
        args = dict(stack_name="node", ram_mb=2048, vcpus=2, runner_slots=2, workers=2, shards=2)
        return sim.simulate(**{**args, **overrides})

    def test_defaults_fit(self):
        result = self.simulate()
        self.assertEqual((result["status"], result["peak_ram_mb"], result["recommendations"]), ("FITS", 850, []))

    def test_heap_cap_is_split_across_processes(self):
        self.assertIn("--max-old-space-size=512", self.simulate()["heap_flag"])
        self.assertIn("768MiB", self.simulate(stack_name="go")["heap_flag"])

    def test_too_many_workers_gets_one_consistent_recommendation(self):
        result = self.simulate(workers=8)
        self.assertEqual(result["status"], "OOM_KILL")
        about_workers = [r for r in result["recommendations"] if "workers" in r]
        self.assertEqual(len(about_workers), 1)
        self.assertIn("to 2", about_workers[0])

    def test_runner_too_small_for_one_worker_says_so(self):
        result = self.simulate(ram_mb=512, workers=1)
        self.assertEqual(result["status"], "OOM_KILL")
        self.assertTrue(any("too small" in r for r in result["recommendations"]))

    def test_shards_beyond_slots_and_slow_test_jobs_are_flagged(self):
        self.assertTrue(any("slots" in r for r in self.simulate(shards=8)["recommendations"]))
        self.assertTrue(any("timeout" in r for r in self.simulate(workers=1, shards=1)["recommendations"]))

    def test_exit_codes(self):
        self.assertEqual(run(sim.main), 0)
        self.assertEqual(run(sim.main, "--workers", "8"), 1)
        for flag in ("--ram", "--vcpus", "--slots", "--workers", "--shards"):
            with self.subTest(flag):
                self.assertEqual(run(sim.main, flag, "0"), 2)
                self.assertEqual(run(sim.main, flag, "-3"), 2)


if __name__ == "__main__":
    unittest.main()
