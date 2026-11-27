"""평가 통계: 문항 단위로 묶은 부트스트랩 신뢰구간과 짝지은 차이.

같은 문항을 여러 번 반복 실행하므로 행(문항×반복)을 독립으로 보면 구간이 지나치게 좁아진다.
그래서 문항을 뽑고 그 문항의 반복을 통째로 따라오게 한다. 난수 씨앗은 고정이다.
"""

from __future__ import annotations

import math
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


def pass_hat_k(by_case: dict[str, list[float]], k: int) -> dict[str, float | int | None]:
    """같은 문항을 k번 돌렸을 때 k번 모두 통과할 비율의 추정치(τ-bench 의 pass^k).

    문항마다 n번 실행 중 c번 통과했다면 그 문항의 값은 C(c, k) / C(n, k) 이고, 문항 평균을 낸다.
    k=1 이면 평균 통과율과 같고, k=n 이면 "n번 모두 통과한 문항의 비율"이다. 한 번 통과와 매번 통과는 다르다:
    통과율 90%인 에이전트도 같은 문의를 세 번 받으면 한 번은 틀릴 수 있다. n < k 인 문항은 뺀다.
    """
    values = []
    for runs in by_case.values():
        n, c = len(runs), int(sum(1 for v in runs if v >= 1.0))
        if n >= k:
            values.append(math.comb(c, k) / math.comb(n, k))
    if not values:
        return {"k": k, "value": None, "cases": 0, "all_pass_cases": 0}
    return {
        "k": k,
        "value": round(sum(values) / len(values), 4),
        "cases": len(values),
        "all_pass_cases": sum(1 for v in values if v == 1.0),
    }


def group(rows: Sequence[Mapping[str, Any]], key: str, value: str) -> dict[str, list[float]]:
    out: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        v = row.get(value)
        if v is not None:
            out[str(row[key])].append(float(v))
    return dict(out)


def cohen_kappa(pairs: Sequence[tuple[str, str]]) -> dict[str, float | int | None]:
    """두 판정자의 일치도. 단순 일치율과, 우연히 맞을 확률을 뺀 Cohen's kappa.

    kappa = (관찰 일치율 − 우연 일치율) / (1 − 우연 일치율). 한쪽이 한 가지 답만 했으면 분모가 0이 되어 None 이다.
    표본이 작으면(수십 건) kappa 의 폭이 넓다. 건수와 함께 읽는다.
    """
    n = len(pairs)
    if n == 0:
        return {"n": 0, "agree": 0, "agreement": None, "kappa": None}
    agree = sum(1 for a, b in pairs if a == b)
    observed = agree / n
    labels = {x for pair in pairs for x in pair}
    expected = sum((sum(1 for a, _ in pairs if a == label) / n) * (sum(1 for _, b in pairs if b == label) / n) for label in labels)
    kappa = None if expected >= 1.0 else round((observed - expected) / (1 - expected), 4)
    return {"n": n, "agree": agree, "agreement": round(observed, 4), "kappa": kappa}
