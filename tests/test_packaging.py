import tomllib
from pathlib import Path

ROOT = Path(__file__).parent.parent
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]


def test_license_is_declared_and_shipped() -> None:
    assert PROJECT["license"] == "MIT"
    assert PROJECT["license-files"] == ["LICENSE"]
    text = (ROOT / "LICENSE").read_text()
    assert text.startswith("MIT License") and "Permission is hereby granted" in text


def test_readme_and_metadata_files_exist() -> None:
    assert (ROOT / PROJECT["readme"]).exists()
    for name in ("CONTRIBUTING.md", "SECURITY.md", ".github/pull_request_template.md"):
        assert (ROOT / name).exists(), name


def test_declared_python_matches_ci() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert PROJECT["requires-python"] == ">=3.11"
    for version in ("3.11", "3.12", "3.13"):
        assert f"Programming Language :: Python :: {version}" in PROJECT["classifiers"]
        assert f'"{version}"' in ci


def test_readme_links_the_policy_files() -> None:
    readme = (ROOT / "README.md").read_text()
    for target in ("LICENSE", "CONTRIBUTING.md", "SECURITY.md"):
        assert f"]({target})" in readme, target
