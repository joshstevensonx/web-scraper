"""Record quality score (section 19).

Completeness 30% · Field validity 25% · Target match 20% · Source reliability 15% · Uniqueness 10%
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..job.models import FieldSpec

WEIGHTS = {"completeness": 30, "validity": 25, "target_match": 20, "source_reliability": 15, "uniqueness": 10}


def quality_score(specs: List[FieldSpec], values: Dict[str, Any], attempted: Dict[str, bool],
                  provenance: Dict[str, Any], target_score: float, source_score: float,
                  has_strong_id: bool) -> Dict[str, Any]:
    total_w = sum(s.weight for s in specs) or 1.0
    filled_w = sum(s.weight for s in specs if values.get(s.name) not in (None, "", []))
    completeness = filled_w / total_w

    tried = [s.name for s in specs if attempted.get(s.name)]
    valid = [n for n in tried if values.get(n) not in (None, "", [])]
    validity = (len(valid) / len(tried)) if tried else 1.0

    confidences = []
    for name, prov in provenance.items():
        items = prov if isinstance(prov, list) else [prov]
        for p in items:
            if isinstance(p, dict) and "confidence" in p:
                confidences.append(float(p["confidence"]))
    method_conf = sum(confidences) / len(confidences) if confidences else 0.5
    reliability = max(0.0, min(1.0, method_conf * (0.6 + 0.4 * source_score)))

    uniqueness = 1.0 if has_strong_id else 0.4
    parts = {
        "completeness": completeness,
        "validity": validity,
        "target_match": max(0.0, min(1.0, target_score)),
        "source_reliability": reliability,
        "uniqueness": uniqueness,
    }
    score = sum(parts[k] * WEIGHTS[k] for k in WEIGHTS)
    return {"score": round(score, 1), "components": {k: round(v * 100) for k, v in parts.items()}}
