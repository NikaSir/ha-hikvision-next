"""Verify that CI exercises the runtime dependencies declared to Home Assistant."""

from importlib.metadata import version
import json
from pathlib import Path

from packaging.requirements import Requirement
import pytest


MANIFEST = Path(__file__).resolve().parents[1] / "custom_components/hikvision_next/manifest.json"


@pytest.mark.parametrize("declaration", json.loads(MANIFEST.read_text())["requirements"])
def test_installed_runtime_dependency_matches_manifest(declaration):
    requirement = Requirement(declaration)
    if requirement.marker is not None and not requirement.marker.evaluate():
        pytest.skip("Dependency is not required on this platform")
    installed = version(requirement.name)
    assert requirement.specifier.contains(installed, prereleases=True), (
        f"CI installed {requirement.name}=={installed}, "
        f"but Home Assistant will install {declaration}"
    )
