# ─────────────────────────────────────────────────────────────────
# VEYDUS — Architectural Boundary Enforcement Test (HLD §16)
# ─────────────────────────────────────────────────────────────────
# What:  Scans all Python source files in veydus/ to ensure that access
#        control columns (department_id, hierarchy_level) do NOT appear
#        in raw SQL strings outside the designated chokepoints.
# How:   Uses Python AST and regex inspection to scan string literals
#        and SQL queries across backend/src/veydus/.
# Why:   HLD §16 mandate: Access predicates must be generated solely
#        in authz/policy.py (and executed in db/repositories/chunks.py).
#        Preventing ad-hoc SQL predicates stops subtle authorization
#        bypasses and keeps the system ready for ReBAC migration (ADR-0003).
# Tools: pytest, ast, pathlib, re
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import ast
import re
from pathlib import Path

# Paths permitted to reference department_id and hierarchy_level in SQL or query logic
_PERMITTED_RELATIVE_PATHS = {
    Path("authz/policy.py"),
    Path("db/repositories/chunks.py"),
}

# The access control attributes that must not appear in ad-hoc SQL strings
_FORBIDDEN_ATTRIBUTES = ["department_id", "hierarchy_level"]

# Basic pattern detecting SQL statements containing the forbidden attributes
_SQL_PATTERNS = [
    re.compile(
        rf"\b(SELECT|INSERT|UPDATE|DELETE|WHERE|AND|OR)\b.*?\b{attr}\b", re.IGNORECASE | re.DOTALL
    )
    for attr in _FORBIDDEN_ATTRIBUTES
]


def test_no_forbidden_sql_outside_chokepoints() -> None:
    """Fail the build if department_id or hierarchy_level appears in SQL outside designated files."""
    src_dir = Path(__file__).resolve().parents[2] / "src" / "veydus"
    violations: list[str] = []

    for py_file in src_dir.rglob("*.py"):
        rel_path = py_file.relative_to(src_dir)
        if rel_path in _PERMITTED_RELATIVE_PATHS:
            continue

        try:
            content = py_file.read_text(encoding="utf-8")
            tree = ast.parse(content, filename=str(py_file))
        except Exception as e:
            violations.append(f"Failed to parse {rel_path}: {e}")
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                string_val = node.value
                for pattern in _SQL_PATTERNS:
                    if pattern.search(string_val):
                        violations.append(
                            f"Prohibited access attribute in SQL string at {rel_path}:{node.lineno}: {string_val.strip()[:60]}..."
                        )

    assert not violations, (
        "HLD §16 architectural violation: access control columns found in SQL outside authorized chokepoints:\n"
        + "\n".join(violations)
    )
