"""Development packages retain the root and QEMU bytes that were built."""

import importlib.util
import json
from pathlib import Path
import subprocess
import tarfile

import pytest


SPEC = importlib.util.spec_from_file_location(
    "development_package", Path(__file__).resolve().parents[1] / "tools/package-qemu.py")
package = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(package)


def git(repository, *arguments):
    return subprocess.check_output(
        ["git", "-c", "user.name=Package test", "-c", "user.email=package@example.invalid",
         *arguments], cwd=repository, text=True).strip()


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    root = tmp_path / "root"
    native = root / "qemu/upstream"
    native.mkdir(parents=True)
    for repository in (root, native):
        git(repository, "init", "-q")
        (repository / "source.c").write_text("committed source\n")
        git(repository, "add", "source.c")
        git(repository, "commit", "-qm", "Initial source")
    native_commit = git(native, "rev-parse", "HEAD")
    (root / "qemu.lock").write_text(f"fork_commit={native_commit}\n")
    git(root, "add", "qemu.lock")
    git(root, "update-index", "--add", "--cacheinfo", f"160000,{native_commit},qemu/upstream")
    git(root, "commit", "-qm", "Pin QEMU")
    monkeypatch.setattr(package, "ROOT", root)
    monkeypatch.setattr(package, "NATIVE", native)
    return root, native


def test_development_archives_both_dirty_worktrees(checkout, tmp_path):
    root, native = checkout
    (root / "source.c").write_text("current host source\n")
    (native / "source.c").write_text("current QEMU source\n")
    (native / "new-source.c").write_text("new QEMU source\n")
    root_commit, native_commit = package.source_identities(development=True)
    sources = tmp_path / "sources"
    sources.mkdir()
    snapshots = [package.archive_worktree(root_commit, sources),
                 package.archive_worktree(native_commit, sources, repository=native, name="qemu")]
    for snapshot, repository in zip(snapshots, (root, native)):
        with tarfile.open(tmp_path / snapshot["path"]) as archive:
            source = next(member for member in archive if member.name.endswith("/source.c"))
            assert archive.extractfile(source).read() == (repository / "source.c").read_bytes()
        assert snapshot["uncommitted"] is True
    native_manifest = json.loads((sources / "qemu-development-worktree.json").read_text())
    assert "new-source.c" in native_manifest["files"]
    assert (sources / "development-worktree.json").is_file()


def test_release_still_rejects_dirty_qemu(checkout):
    _, native = checkout
    (native / "source.c").write_text("uncommitted QEMU\n")
    with pytest.raises(subprocess.CalledProcessError):
        package.source_identities()
    assert package.source_identities(development=True)[1] == git(native, "rev-parse", "HEAD")


def test_development_still_requires_matching_qemu_pin(checkout):
    root, _ = checkout
    (root / "qemu.lock").write_text("fork_commit=wrong\n")
    with pytest.raises(ValueError, match="must agree"):
        package.source_identities(development=True)
