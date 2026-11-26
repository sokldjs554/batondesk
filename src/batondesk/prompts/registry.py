"""프롬프트 버전 저장소.

프롬프트는 코드에 박아 두지 않고 `prompts/<종류>/<버전>.yaml` 파일로 둔다.
- 버전 파일은 한 번 평가에 쓰이면 고치지 않는다. 바꾸려면 새 버전 파일을 만든다.
  `prompts/LOCK.json` 에 버전별 sha256 을 적어 두고, 내용이 달라지면 CI가 실패한다.
- 어떤 버전을 쓸지는 `prompts/manifest.yaml` 의 active 가 정하고, 환경 변수로 덮어쓸 수 있다.
- 실행 기록에는 버전 ID와 해시를 함께 남겨, 어떤 프롬프트로 나온 답인지 추적할 수 있게 한다.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

PromptKind = Literal["agent", "grounding", "resolution"]
KINDS: tuple[PromptKind, ...] = ("agent", "grounding", "resolution")


class PromptError(ValueError):
    pass


@dataclass(frozen=True)
class PromptVersion:
    id: str
    kind: PromptKind
    created: str
    summary: str
    changes: str
    system: str
    tool_descriptions: dict[str, str] = field(default_factory=dict)
    path: str = ""

    @property
    def sha256(self) -> str:
        canonical = json.dumps(
            {"system": self.system, "tool_descriptions": self.tool_descriptions},
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "created": self.created,
            "summary": self.summary,
            "changes": self.changes,
            "sha256": self.sha256,
            "system_chars": len(self.system),
        }


def _load_file(path: Path, kind: PromptKind) -> PromptVersion:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    expected = f"{kind}-{path.stem}"
    if raw.get("id") != expected:
        raise PromptError(f"{path}: id 는 {expected!r} 여야 합니다(현재 {raw.get('id')!r})")
    required = ["created", "summary", "system"]
    if path.stem != "v1":
        required.append("changes")  # 두 번째 버전부터는 무엇을 왜 바꿨는지 적어야 한다
    for key in required:
        if not raw.get(key):
            raise PromptError(f"{path}: {key} 가 비어 있습니다")
    return PromptVersion(
        id=raw["id"],
        kind=kind,
        created=str(raw["created"]),
        summary=str(raw["summary"]).strip(),
        changes=str(raw.get("changes") or "").strip(),
        system=str(raw["system"]).strip(),
        tool_descriptions={k: str(v).strip() for k, v in (raw.get("tool_descriptions") or {}).items()},
        path=str(path),
    )


class PromptRegistry:
    def __init__(self, prompts_dir: Path) -> None:
        self.dir = prompts_dir
        self.versions: dict[str, PromptVersion] = {}
        for kind in KINDS:
            for path in sorted((prompts_dir / kind).glob("v*.yaml"), key=lambda p: int(p.stem[1:])):
                version = _load_file(path, kind)
                self.versions[version.id] = version
        manifest_path = prompts_dir / "manifest.yaml"
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        self.active: dict[str, str] = dict(manifest.get("active") or {})
        for kind in KINDS:
            if self.active.get(kind) not in self.versions:
                raise PromptError(f"manifest.yaml: active.{kind} 가 없는 버전입니다: {self.active.get(kind)!r}")

    def get(self, kind: PromptKind, version_id: str | None = None) -> PromptVersion:
        vid = version_id or self.active[kind]
        version = self.versions.get(vid)
        if version is None or version.kind != kind:
            raise PromptError(f"{kind} 프롬프트 버전이 없습니다: {vid!r}")
        return version

    def all_versions(self, kind: PromptKind | None = None) -> list[PromptVersion]:
        return [v for v in self.versions.values() if kind is None or v.kind == kind]

    # ---- 잠금 -----------------------------------------------------------------

    @property
    def lock_path(self) -> Path:
        return self.dir / "LOCK.json"

    def read_lock(self) -> dict[str, str]:
        if not self.lock_path.exists():
            return {}
        data: dict[str, str] = json.loads(self.lock_path.read_text(encoding="utf-8"))
        return data

    def check_lock(self) -> list[str]:
        """잠긴 버전의 내용이 바뀌었으면 문제 목록을 돌려준다."""
        problems = []
        for vid, digest in self.read_lock().items():
            version = self.versions.get(vid)
            if version is None:
                problems.append(f"{vid}: 잠긴 버전 파일이 없어졌습니다")
            elif version.sha256 != digest:
                problems.append(f"{vid}: 잠긴 버전의 내용이 바뀌었습니다. 기존 파일은 되돌리고 새 버전을 만드세요")
        return problems

    def write_lock(self, version_ids: list[str] | None = None) -> dict[str, str]:
        lock = self.read_lock()
        for vid in version_ids or list(self.versions):
            lock.setdefault(vid, self.versions[vid].sha256)
        self.lock_path.write_text(json.dumps(dict(sorted(lock.items())), indent=2) + "\n", encoding="utf-8")
        return lock
