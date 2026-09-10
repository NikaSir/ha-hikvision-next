"""Execute the required gate without Home Assistant or YAML dependencies."""

import json
import os
from pathlib import Path
import re
import subprocess
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ("repository-checks", "hassfest")


class RequiredCIGateTests(unittest.TestCase):
    def setUp(self):
        self.source = (ROOT / ".github/workflows/testing.yml").read_text()
        self.jobs = dict(re.findall(
            r"^  ([\w-]+):\n(.*?)(?=^  [\w-]+:|\Z)",
            self.source.split("\njobs:\n", 1)[1], re.M | re.S,
        ))

    def gate(self):
        self.assertIn("validate", self.jobs,
                      "Required validate must cover pytest and Hassfest")
        return self.jobs["validate"]

    def success(self):
        return {name: {"result": "success", "outputs": {}} for name in REQUIRED}

    def run_gate(self, results):
        script = re.search(r"^        run: \|\n((?:          .*\n|\n)+)", self.gate(), re.M)
        self.assertIsNotNone(script)
        env = os.environ.copy()
        env["NIKAS_JOB_RESULTS"] = json.dumps(results)
        return subprocess.run(
            ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c",
             textwrap.dedent(script.group(1))],
            env=env, text=True, capture_output=True, timeout=5,
        )

    def test_required_gate_covers_pytest_and_hassfest_without_bypass(self):
        gate = self.gate()
        self.assertEqual(set(self.jobs), {"validate", *REQUIRED})
        self.assertRegex(gate, r"(?m)^    needs: \[repository-checks, hassfest\]$")
        self.assertRegex(gate, r"(?m)^    if: \$\{\{ always\(\) \}\}$")
        self.assertRegex(gate, r"(?m)^          NIKAS_JOB_RESULTS: \$\{\{ toJSON\(needs\) \}\}$")
        self.assertRegex(gate, r"(?m)^        shell: bash$")
        self.assertNotRegex(gate, r"(?m)^        if:")
        self.assertEqual(len(re.findall(r"^      - ", gate, re.M)), 1)
        self.assertNotIn("continue-on-error:", self.source)
        self.assertIn("pytest", self.jobs["repository-checks"])
        self.assertIn("home-assistant/actions/hassfest@", self.jobs["hassfest"])
        triggers = self.source.split("\njobs:\n", 1)[0]
        self.assertRegex(triggers, r"(?m)^  push:$")
        self.assertRegex(triggers, r"(?m)^  pull_request:$")
        self.assertNotRegex(triggers, r"(?m)^\s+paths(?:-ignore)?:")

    def test_validate_context_is_unique_and_hacs_remains_separate(self):
        names = []
        for path in (ROOT / ".github/workflows").iterdir():
            if path.suffix not in (".yml", ".yaml"):
                continue
            source = path.read_text().split("\njobs:\n", 1)[1]
            for job_id, block in re.findall(
                r"^  ([\w-]+):\n(.*?)(?=^  [\w-]+:|\Z)", source, re.M | re.S,
            ):
                name = re.search(r"^    name: (.+)$", block, re.M)
                names.append(name.group(1).strip('"\'') if name else job_id)
        self.assertEqual(names.count("validate"), 1)
        self.assertEqual(names.count("HACS Action"), 1)

    def test_complete_success_passes(self):
        result = self.run_gate(self.success())
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unsuccessful_missing_and_unexpected_results_fail(self):
        cases = [{}, {**self.success(), "unexpected": {"result": "success"}}]
        for name in REQUIRED:
            for status in ("failure", "cancelled", "skipped", "neutral", "", None):
                results = self.success()
                results[name]["result"] = status
                cases.append(results)
            results = self.success()
            del results[name]
            cases.append(results)
            results = self.success()
            results[name] = {}
            cases.append(results)
        for results in cases:
            with self.subTest(results=results):
                self.assertNotEqual(self.run_gate(results).returncode, 0)


if __name__ == "__main__":
    unittest.main()
