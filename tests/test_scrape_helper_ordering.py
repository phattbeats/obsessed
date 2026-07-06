"""Regression test for PHA-1322: nested public-records helpers in trigger_scrape
were defined AFTER their call sites, so any profile with news_query/court_query/
sos_query/auditor_query raised NameError (swallowed by the broad except).

Guards the ordering statically: each nested `async def _scrape_*` must appear
before every reference to that name inside trigger_scrape.
"""
import ast
from pathlib import Path

PROFILES = Path(__file__).resolve().parents[1] / "app" / "routes" / "profiles.py"
HELPERS = ["_scrape_news", "_scrape_court", "_scrape_sos", "_scrape_auditor"]


def _trigger_scrape_node():
    tree = ast.parse(PROFILES.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "trigger_scrape":
            return node
    raise AssertionError("trigger_scrape not found in app/routes/profiles.py")


def test_scrape_helpers_defined_before_use():
    fn = _trigger_scrape_node()
    def_lines = {}
    use_lines = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.AsyncFunctionDef) and node.name in HELPERS:
            def_lines[node.name] = node.lineno
        elif isinstance(node, ast.Name) and node.id in HELPERS:
            use_lines.setdefault(node.id, node.lineno)
            use_lines[node.id] = min(use_lines[node.id], node.lineno)

    for name in HELPERS:
        assert name in def_lines, f"{name} is no longer defined in trigger_scrape"
        assert name in use_lines, f"{name} is never called in trigger_scrape"
        assert def_lines[name] < use_lines[name], (
            f"{name} defined at line {def_lines[name]} but first used at "
            f"line {use_lines[name]} — def-after-use NameError regression"
        )
