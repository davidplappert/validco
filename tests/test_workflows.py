"""Tests that CI actually runs the tests.

The backend suite is split across parallel runners by **enumerating file
paths**, which keeps a failure named — "integration failed", not "tests failed"
— and stops the slowest suite from setting the pace for all of them.

The cost of that split is a quiet trap: adding ``tests/test_thing.py`` does
nothing unless someone also edits two YAML files. Three suites had already
fallen through it (``test_place``, ``test_regions``, ``test_suggest``), and the
pull-request workflow was additionally missing the security and privacy guards
entirely — so the check that keeps a home address out of a public repository was
not running on the pull requests where it would have mattered most.

A test suite that silently does not run is worse than one that fails, because it
reports success. These tests close the loop.
"""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

#: Workflows expected to run the backend suite, and the job within each.
BACKEND_JOBS = {"ci.yml": "backend", "deploy.yml": "test"}


def all_test_files() -> set[str]:
    """Every backend test file, as a repo-relative path."""
    return {f"tests/{p.name}" for p in (ROOT / "tests").glob("test_*.py")}


def enumerated_paths(workflow: str, job: str) -> set[str]:
    """The union of every path the given job's matrix enumerates."""
    document = yaml.safe_load((WORKFLOWS / workflow).read_text())
    matrix = document["jobs"][job]["strategy"]["matrix"]["include"]
    return {path for entry in matrix for path in str(entry["paths"]).split()}


@pytest.mark.parametrize(("workflow", "job"), sorted(BACKEND_JOBS.items()))
def test_every_test_file_runs_in_ci(workflow: str, job: str):
    """No test file may exist without a runner executing it.

    This file included: it is in ``tests/`` and so must appear in the matrix
    like any other, which conveniently means the guard guards itself.
    """
    missing = all_test_files() - enumerated_paths(workflow, job)
    assert not missing, (
        f"{workflow} never runs {sorted(missing)}. Add them to a suite in the "
        f"'{job}' job's matrix — a test file that no runner executes reports "
        f"success by not existing."
    )


@pytest.mark.parametrize(("workflow", "job"), sorted(BACKEND_JOBS.items()))
def test_the_matrix_names_no_file_that_is_gone(workflow: str, job: str):
    """A renamed or deleted file must not linger in the matrix.

    ``pytest`` exits 4 on an unrecognised path, so a stale entry fails the whole
    suite with a usage error rather than a test failure — an unhelpful way to
    learn that a file was renamed.
    """
    phantom = enumerated_paths(workflow, job) - all_test_files()
    assert not phantom, f"{workflow} names files that no longer exist: {sorted(phantom)}"


def test_both_workflows_run_the_same_suite():
    """A pull request must run exactly what a deploy runs.

    Otherwise "green on the PR" means less than it appears to: this is precisely
    how ``ci.yml`` came to omit the security and privacy suites while
    ``deploy.yml`` ran them.
    """
    ci = enumerated_paths("ci.yml", "backend")
    deploy = enumerated_paths("deploy.yml", "test")
    assert ci == deploy, (
        f"only in ci.yml: {sorted(ci - deploy)}; only in deploy.yml: {sorted(deploy - ci)}"
    )
