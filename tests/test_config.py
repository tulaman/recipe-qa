"""Build version reported by /healthz (SPEC §2.2, ADR 003).

The commit SHA is written into the build context by CI and baked into the
image, so a running container reports the code it runs, not a mutable
service variable.
"""

from pathlib import Path

import pytest

from recipe_qa.config import Settings


def test_version_is_read_from_the_build_sha_file(tmp_path: Path) -> None:
    sha_file = tmp_path / "BUILD_SHA"
    sha_file.write_text("f6984bf0123456789abcdef0123456789abcdef0\n")
    assert Settings.from_env(build_sha_file=sha_file).version == (
        "f6984bf0123456789abcdef0123456789abcdef0"
    )


def test_version_is_dev_without_a_build_sha_file(tmp_path: Path) -> None:
    assert Settings.from_env(build_sha_file=tmp_path / "missing").version == "dev"


def test_version_is_dev_for_an_empty_build_sha_file(tmp_path: Path) -> None:
    sha_file = tmp_path / "BUILD_SHA"
    sha_file.write_text("\n")
    assert Settings.from_env(build_sha_file=sha_file).version == "dev"


def test_git_sha_variable_does_not_override_the_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A stale service variable must not make an old container claim a new commit.
    sha_file = tmp_path / "BUILD_SHA"
    sha_file.write_text("built-sha\n")
    monkeypatch.setenv("GIT_SHA", "stale-variable")
    assert Settings.from_env(build_sha_file=sha_file).version == "built-sha"
