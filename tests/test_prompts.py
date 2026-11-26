from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from batondesk.prompts.registry import PromptError, PromptRegistry
from batondesk.settings import REPO_ROOT


@pytest.fixture
def copy(tmp_path: Path) -> Path:
    target = tmp_path / "prompts"
    shutil.copytree(REPO_ROOT / "prompts", target)
    return target


def test_registry_loads_every_kind(prompts: PromptRegistry) -> None:
    for kind in ("agent", "grounding", "resolution"):
        version = prompts.get(kind)  # type: ignore[arg-type]
        assert version.kind == kind and version.system and len(version.sha256) == 64
    with pytest.raises(PromptError):
        prompts.get("agent", "agent-v999")
    with pytest.raises(PromptError):
        prompts.get("agent", "grounding-v1")


def test_repository_prompts_match_their_lock(prompts: PromptRegistry) -> None:
    assert prompts.check_lock() == [], "잠긴 프롬프트 버전은 고치지 말고 새 버전을 만든다"


def test_lock_detects_an_edited_version(copy: Path) -> None:
    PromptRegistry(copy).write_lock()
    path = copy / "agent" / "v1.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("정중하고 간결하게", "길고 장황하게"), encoding="utf-8")
    problems = PromptRegistry(copy).check_lock()
    assert len(problems) == 1 and problems[0].startswith("agent-v1")


def test_lock_keeps_existing_hashes(copy: Path) -> None:
    (copy / "LOCK.json").unlink(missing_ok=True)
    registry = PromptRegistry(copy)
    first = registry.write_lock(["agent-v1"])
    assert first == {"agent-v1": registry.get("agent", "agent-v1").sha256}
    assert set(registry.write_lock()) >= {"agent-v1", "grounding-v1", "resolution-v1"}


def test_id_must_match_the_file_name(copy: Path) -> None:
    path = copy / "agent" / "v1.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("id: agent-v1", "id: agent-v7"), encoding="utf-8")
    with pytest.raises(PromptError, match="id 는"):
        PromptRegistry(copy)


def test_manifest_must_point_at_an_existing_version(copy: Path) -> None:
    (copy / "manifest.yaml").write_text(
        "active:\n  agent: agent-v9\n  grounding: grounding-v1\n  resolution: resolution-v1\n", encoding="utf-8"
    )
    with pytest.raises(PromptError, match=r"active\.agent"):
        PromptRegistry(copy)


def test_later_versions_must_say_what_changed(copy: Path) -> None:
    v1 = (copy / "agent" / "v1.yaml").read_text(encoding="utf-8")
    (copy / "agent" / "v2.yaml").write_text(v1.replace("id: agent-v1", "id: agent-v2"), encoding="utf-8")
    with pytest.raises(PromptError, match="changes"):
        PromptRegistry(copy)
