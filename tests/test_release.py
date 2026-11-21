"""Verify release archives against their committed tag."""

import json
import os
import subprocess
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

SCRIPT = Path(__file__).parents[1] / "scripts" / "build_release.py"
ROOT = SCRIPT.parents[1]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())


@pytest.fixture
def release_repo(tmp_path):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()

    git("init", "-q")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")
    integration = tmp_path / "custom_components" / "echonet_lite"
    integration.mkdir(parents=True)
    (integration / "__init__.py").write_text('"""Integration."""\n')
    (integration / "manifest.json").write_bytes(
        (ROOT / "custom_components/echonet_lite/manifest.json").read_bytes()
    )
    (tmp_path / "version.txt").write_text("0.1.5\n")
    (tmp_path / ".release-please-manifest.json").write_text(json.dumps({".": "0.1.5"}))
    (tmp_path / "hacs.json").write_text(
        json.dumps({"filename": "echonet_lite.zip", "zip_release": True})
    )
    (tmp_path / "LICENSE").write_text("Test license\n")
    return tmp_path, git


def build(repo, git, tag="v0.1.5", sha=None):
    env = os.environ | {
        "RELEASE_TAG": tag,
        "RELEASE_SHA": sha or git("rev-parse", "HEAD"),
    }
    return subprocess.run(
        [os.sys.executable, str(SCRIPT)],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )


def commit_and_tag(git):
    git("add", ".")
    git("commit", "-qm", "test")
    git("tag", "v0.1.5")


def test_archive_is_deterministic_and_contains_only_release_content(release_repo):
    repo, git = release_repo
    commit_and_tag(git)
    assert build(repo, git).returncode == 0
    archive_path = repo / "dist" / "echonet_lite.zip"
    original = archive_path.read_bytes()
    with ZipFile(archive_path) as archive:
        assert set(archive.namelist()) == {"__init__.py", "manifest.json", "LICENSE"}
        source = (repo / "custom_components/echonet_lite/manifest.json").read_bytes()
        assert archive.read("manifest.json") == source.replace(b"0.0.0", b"0.1.5")
        assert (
            archive.read("__init__.py")
            == (repo / "custom_components/echonet_lite/__init__.py").read_bytes()
        )
    assert build(repo, git).returncode == 0
    assert archive_path.read_bytes() == original


def test_rejects_mismatched_tag_and_preserves_existing_archive(release_repo):
    repo, git = release_repo
    commit_and_tag(git)
    assert build(repo, git).returncode == 0
    archive_path = repo / "dist" / "echonet_lite.zip"
    original = archive_path.read_bytes()
    git("tag", "v0.1.6")
    result = build(repo, git, tag="v0.1.6")
    assert result.returncode != 0
    assert "Release version files disagree" in result.stderr
    assert archive_path.read_bytes() == original


def test_rejects_symlink_in_archive(release_repo):
    repo, git = release_repo
    (repo / "custom_components" / "echonet_lite" / "link").symlink_to("../../LICENSE")
    commit_and_tag(git)
    result = build(repo, git)
    assert result.returncode != 0
    assert "Unexpected archive member" in result.stderr


@pytest.mark.parametrize(
    ("path", "content", "error"),
    [
        (
            "custom_components/echonet_lite/manifest.json",
            '{\n  "version": "0.1.5"\n}\n',
            "Committed manifest version must be 0.0.0",
        ),
        (
            "custom_components/echonet_lite/manifest.json",
            '{\n  "version": "0.0.0",\n  "version": "0.0.0"\n}\n',
            "Expected exactly one manifest version placeholder",
        ),
        (
            ".release-please-manifest.json",
            '{".": "0.1.4"}',
            "Release version files disagree",
        ),
    ],
)
def test_rejects_inconsistent_release_metadata(release_repo, path, content, error):
    repo, git = release_repo
    (repo / path).write_text(content)
    commit_and_tag(git)
    result = build(repo, git)
    assert result.returncode != 0
    assert error in result.stderr


def test_release_configuration_publishes_a_prerelease():
    config = json.loads((ROOT / "release-please-config.json").read_text())
    assert not config["packages"]["."]["draft"]
    assert config["packages"]["."]["prerelease"]
    assert config["packages"]["."]["force-tag-creation"]
    assert "extra-files" not in config["packages"]["."]
    assert WORKFLOW["jobs"]["release"]["if"] == "github.event_name == 'push'"


def test_version_files_agree_and_support_prerelease_publication():
    version = (ROOT / "version.txt").read_text().strip()
    manifest = json.loads(
        (ROOT / "custom_components/echonet_lite/manifest.json").read_text()
    )
    release_manifest = json.loads((ROOT / ".release-please-manifest.json").read_text())
    assert manifest["version"] == "0.0.0"
    assert version == release_manifest["."]
    assert version.startswith("0.")


def test_release_and_recovery_only_attach_verified_archives():
    events = WORKFLOW[True]
    assert events["workflow_dispatch"]["inputs"]["release_tag"]["required"]
    jobs = WORKFLOW["jobs"]
    package = jobs["package"]
    assert "always()" in package["if"]
    assert "github.event_name == 'workflow_dispatch'" in package["if"]
    assert "needs.recover.result == 'success'" in package["if"]
    assert jobs["recover"]["if"] == (
        "github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/master'"
    )
    assert package["needs"] == ["release", "recover"]
    assert jobs["recover"]["steps"][0]["with"]["ref"] == "${{ github.sha }}"
    assert package["steps"][0]["with"]["ref"] == (
        "${{ needs.recover.outputs.sha || needs.release.outputs.sha }}"
    )
    verify = next(
        step
        for step in package["steps"]
        if step.get("name") == "Verify published pre-release and tag revision"
    )
    assert "isPrerelease" in verify["run"]
    assert "isDraft" in verify["run"]
    upload = package["steps"][-1]
    assert "gh release download" in upload["run"]
    assert "cmp --silent" in upload["run"]
    assert "gh release upload" in upload["run"]
    assert "--clobber" not in json.dumps(WORKFLOW)
    assert "gh release create" not in json.dumps(WORKFLOW)
    assert "isPrerelease" in jobs["recover"]["steps"][1]["run"]
