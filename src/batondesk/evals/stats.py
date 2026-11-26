"""평가 통계: 문항 단위로 묶은 부트스트랩 신뢰구간과 짝지은 차이.

같은 문항을 여러 번 반복 실행하므로 행(문항×반복)을 독립으로 보면 구간이 지나치게 좁아진다.
그래서 문항을 뽑고 그 문항의 반복을 통째로 따라오게 한다. 난수 씨앗은 고정이다.
"""

from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

SEED = 20261126
ITERATIONS = 2000


def mean(values: Iterable[float]) -> float | None:
    items = list(values)
    return sum(items) / len(items) if items else None


def cluster_ci(by_case: dict[str, list[float]], *, iterations: int = ITERATIONS, seed: int = SEED) -> dict[str, float | int | None]:
    """by_case: 문항 id → 그 문항의 반복별 값(0/1 또는 실수)."""
    cases = [values for values in by_case.values() if values]
    rows = [v for values in cases for v in values]
    if not rows:
        return {"mean": None, "lo": None, "hi": None, "cases": 0, "rows": 0}
    point = sum(rows) / len(rows)
    rng = random.Random(seed)
    samples = []
    for _ in range(iterations):
        picked = [cases[rng.randrange(len(cases))] for _ in cases]
        flat = [v for values in picked for v in values]
        samples.append(sum(flat) / len(flat))
    samples.sort()
    return {
        "mean": round(point, 4),
        "lo": round(samples[int(0.025 * iterations)], 4),
        "hi": round(samples[min(iterations - 1, int(0.975 * iterations))], 4),
        "cases": len(cases),
        "rows": len(rows),
    }


def paired_diff(
    a: dict[str, list[float]], b: dict[str, list[float]], *, iterations: int = ITERATIONS, seed: int = SEED
) -> dict[str, float | int | None]:
    """같은 문항에서 b − a 의 평균 차이(문항별 반복 평균끼리 짝지음)와 95% 구간."""
    shared = sorted(k for k in a if k in b and a[k] and b[k])
    if not shared:
        return {"diff": None, "lo": None, "hi": None, "cases": 0}
    deltas = [sum(b[k]) / len(b[k]) - sum(a[k]) / len(a[k]) for k in shared]
    rng = random.Random(seed)
    samples = []
    for _ in range(iterations):
        picked = [deltas[rng.randrange(len(deltas))] for _ in deltas]
        samples.append(sum(picked) / len(picked))
    samples.sort()
    return {
        "diff": round(sum(deltas) / len(deltas), 4),
        "lo": round(samples[int(0.025 * iterations)], 4),
        "hi": round(samples[min(iterations - 1, int(0.975 * iterations))], 4),
        "cases": len(shared),
        "improved": sum(1 for d in deltas if d > 0),
        "regressed": sum(1 for d in deltas if d < 0),
    }


def group(rows: Sequence[Mapping[str, Any]], key: str, value: str) -> dict[str, list[float]]:
    out: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        v = row.get(value)
        if v is not None:
            out[str(row[key])].append(float(v))
    return dict(out)
