"""Record validation (section 18): schema, field format, target, completeness."""

from __future__ import annotations

import re
from typing import Any, Dict, List

from ..job.models import FieldSpec


def check(field: str, category: str, passed: bool, message: str = "") -> Dict[str, Any]:
    return {"field": field, "category": category, "passed": bool(passed), "message": message}


def validate_fields(specs: List[FieldSpec], values: Dict[str, Any], format_errors: Dict[str, str]) -> List[Dict[str, Any]]:
    """Schema/format/completeness checks for one normalized record."""
    results: List[Dict[str, Any]] = []
    for spec in specs:
        value = values.get(spec.name)
        present = value not in (None, "", [])
        if spec.required:
            results.append(check(spec.name, "schema", present, "present" if present else "required field missing"))
        if spec.name in format_errors and not present:
            results.append(check(spec.name, "format", False, format_errors[spec.name]))
        elif present:
            ok = True
            msg = "valid %s" % spec.type
            if spec.validation:
                items = value if isinstance(value, list) else [value]
                ok = all(re.search(spec.validation, str(v)) for v in items)
                msg = "matches validation rule" if ok else "does not match validation rule %r" % spec.validation
            results.append(check(spec.name, "format", ok, msg))
    return results


def required_ok(results: List[Dict[str, Any]]) -> bool:
    return all(r["passed"] for r in results if r["category"] == "schema")
