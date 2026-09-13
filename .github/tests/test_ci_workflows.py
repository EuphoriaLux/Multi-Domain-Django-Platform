"""Execute the actual workflow gate scripts with success/failure/skip fixtures.

Run without Django: python -m unittest discover -s .github/tests -v
Requires PyYAML, installed by the always-running scope job.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


def workflow(name):
    # BaseLoader keeps GitHub's YAML 1.2 `on` as a string (not YAML 1.1 True).
    return yaml.load((WORKFLOWS / name).read_text(encoding="utf-8"), yaml.BaseLoader)


CI = workflow("test-and-validate.yml")
SECURITY = workflow("security-scan.yml")


def run_python_step(step, env, cwd=ROOT):
    assert step["shell"] == "python"
    return subprocess.run(
        [sys.executable, "-c", step["run"]],
        cwd=cwd,
        env={**os.environ, **env},
        text=True,
        capture_output=True,
        check=False,
    )


def gate_needs(name, platform=True):
    needs = {job: {"result": "success"} for job in CI["jobs"][name]["needs"]}
    needs["changes"]["outputs"] = {"run-platform": str(platform).lower()}
    if not platform:
        for job in ("run-tests", "javascript-lint", "django-deploy-check"):
            if job in needs:
                needs[job]["result"] = "skipped"
    return needs


class AggregateGateTests(unittest.TestCase):
    def check_gate(self, name, needs, passes):
        result = run_python_step(
            CI["jobs"][name]["steps"][0], {"CI_NEEDS": json.dumps(needs)}
        )
        self.assertEqual(result.returncode == 0, passes, result.stdout + result.stderr)

    def test_success_and_explicit_platform_skip(self):
        for name in ("test", "validate"):
            for platform in (True, False):
                with self.subTest(name=name, platform=platform):
                    self.check_gate(name, gate_needs(name, platform), True)

    def test_every_failed_cancelled_or_unexpectedly_skipped_job_blocks(self):
        # Execute the scripts GitHub uses, including the formerly ignored Ruff
        # and dependency audit job failures. Cancellation must not turn green.
        for name in ("test", "validate"):
            for job in CI["jobs"][name]["needs"]:
                for state in ("failure", "cancelled", "skipped"):
                    with self.subTest(name=name, job=job, state=state):
                        needs = gate_needs(name)
                        needs[job]["result"] = state
                        self.check_gate(name, needs, False)

    def test_docs_pr_still_requires_lint_and_security(self):
        for job in ("changes", "lint", "security-check"):
            with self.subTest(job=job):
                needs = gate_needs("validate", platform=False)
                needs[job]["result"] = "failure"
                self.check_gate("validate", needs, False)

    def test_missing_or_invalid_scope_fails_closed(self):
        for name in ("test", "validate"):
            for scope in (None, "", "invalid"):
                with self.subTest(name=name, scope=scope):
                    needs = gate_needs(name, platform=False)
                    needs["changes"]["outputs"] = {"run-platform": scope}
                    self.check_gate(name, needs, False)

    def test_security_summary_rejects_every_unsuccessful_scan(self):
        job = SECURITY["jobs"]["security-summary"]
        baseline = {name: {"result": "success"} for name in job["needs"]}
        result = run_python_step(job["steps"][0], {"CI_NEEDS": json.dumps(baseline)})
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in job["needs"]:
            for state in ("failure", "cancelled", "skipped"):
                with self.subTest(name=name, state=state):
                    needs = {**baseline, name: {"result": state}}
                    result = run_python_step(
                        job["steps"][0], {"CI_NEEDS": json.dumps(needs)}
                    )
                    self.assertNotEqual(result.returncode, 0)


class WorkflowWiringTests(unittest.TestCase):
    def test_every_pr_path_reaches_aggregate_workflows(self):
        for data in (CI, SECURITY):
            trigger = data["on"]["pull_request"]
            self.assertNotIn("paths", trigger)
            self.assertNotIn("paths-ignore", trigger)
            self.assertEqual(trigger["branches"], ["main", "develop"])

    def test_aggregate_jobs_run_after_failures_and_cancellations(self):
        for data, jobs in (
            (CI, ("test", "validate")),
            (SECURITY, ("security-summary",)),
        ):
            for name in jobs:
                self.assertEqual(data["jobs"][name]["if"], "always()")
                self.assertNotIn("continue-on-error", data["jobs"][name])

    def test_only_explicitly_advisory_steps_can_ignore_errors(self):
        advisory_commands = {
            "black --check --diff .",
            "ruff check . --statistics",
            "npm run format:check",
        }
        found = set()
        for job in CI["jobs"].values():
            self.assertNotIn("continue-on-error", job)
            for step in job["steps"]:
                if step.get("continue-on-error") == "true":
                    found.add(step["run"].strip())
        self.assertEqual(found, advisory_commands)
        self.assertIn("lint", CI["jobs"]["validate"]["needs"])
        self.assertIn("security-check", CI["jobs"]["validate"]["needs"])

    def test_expensive_jobs_and_scope_regressions_are_wired(self):
        for name in ("run-tests", "javascript-lint", "django-deploy-check"):
            self.assertEqual(CI["jobs"][name]["needs"], "changes")
            self.assertEqual(
                CI["jobs"][name]["if"], "needs.changes.outputs.run-platform == 'true'"
            )
        steps = CI["jobs"]["changes"]["steps"]
        self.assertTrue(
            any("unittest discover -s .github/tests" in s.get("run", "") for s in steps)
        )
        self.assertEqual(steps[0]["with"]["fetch-depth"], "2")

    def test_security_exception_is_consistent(self):
        for data, name in ((CI, "security-check"), (SECURITY, "dependency-scan")):
            scan = next(
                s for s in data["jobs"][name]["steps"] if s["name"] == "Run pip-audit"
            )
            self.assertIn("--ignore-vuln PYSEC-2025-183", scan["run"])
            self.assertNotIn("continue-on-error", scan)


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.email", "ci@example.invalid")
        self.git("config", "user.name", "CI fixture")
        self.git("config", "commit.gpgsign", "false")
        self.write_file("README.md")
        self.git("add", ".")
        self.git("commit", "-qm", "base")
        self.scope_step = next(
            step for step in CI["jobs"]["changes"]["steps"] if step.get("id") == "scope"
        )

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.repo, check=True, capture_output=True
        )

    def write_file(self, path, content="fixture\n"):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def scope(self, event="pull_request"):
        output = self.repo / "output.txt"
        result = run_python_step(
            self.scope_step,
            {"GITHUB_EVENT_NAME": event, "GITHUB_OUTPUT": str(output)},
            cwd=self.repo,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return output.read_text().strip()

    def test_config_unknown_and_function_paths_run_platform_jobs(self):
        # Each case is an isolated single-file PR diff against its first parent.
        for path in (
            "package-lock.json",
            "pyproject.toml",
            "pytest.ini",
            ".gitleaks.toml",
            ".github/workflows/test-and-validate.yml",
            "infra/main.bicep",
            "azure-functions/hybrid-maintenance/local.settings.json.example",
            "new-component/config.unknown",
            "a filename with spaces.txt",
        ):
            with self.subTest(path=path):
                self.write_file(path)
                self.git("add", path)
                self.git("commit", "-qm", "config only")
                self.assertEqual(self.scope(), "run-platform=true")
                (self.repo / "output.txt").unlink()

    def test_markdown_and_native_shell_only_skip_platform_jobs(self):
        self.write_file("docs/guide.md")
        self.write_file("mobile/ios/App.swift")
        self.git("add", ".")
        self.git("commit", "-qm", "docs and native shell")
        self.assertEqual(self.scope(), "run-platform=false")

    def test_renamed_app_code_into_docs_still_runs_platform_jobs(self):
        self.write_file("app.py")
        self.git("add", ".")
        self.git("commit", "-qm", "app")
        (self.repo / "docs").mkdir()
        self.git("mv", "app.py", "docs/app.md")
        self.git("commit", "-qm", "rename app to docs")
        self.assertEqual(self.scope(), "run-platform=true")

    def test_manual_runs_do_not_need_a_pr_parent(self):
        self.assertEqual(self.scope("workflow_dispatch"), "run-platform=true")

    def test_merge_diff_covers_earlier_commits_in_the_pr(self):
        base = self.git("rev-parse", "HEAD").stdout.decode().strip()
        self.git("checkout", "-qb", "feature")
        self.write_file("app.py")
        self.git("add", ".")
        self.git("commit", "-qm", "app change")
        self.write_file("docs/guide.md")
        self.git("add", ".")
        self.git("commit", "-qm", "docs change")
        self.git("checkout", "-q", "--detach", base)
        self.git("merge", "--no-ff", "feature", "-qm", "PR merge")
        self.assertEqual(self.scope(), "run-platform=true")

    def test_empty_diff_runs_platform_jobs(self):
        self.git("commit", "--allow-empty", "-qm", "empty")
        self.assertEqual(self.scope(), "run-platform=true")

    def test_diff_error_fails_instead_of_skipping_tests(self):
        result = run_python_step(
            self.scope_step,
            {
                "GITHUB_EVENT_NAME": "pull_request",
                "GITHUB_OUTPUT": str(self.repo / "out"),
            },
            cwd=self.repo,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.repo / "out").exists())


if __name__ == "__main__":
    unittest.main()
