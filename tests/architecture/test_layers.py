"""Layer rules: each layer may import only from the layers listed for it.

    cli -> pipelines -> {ingestion, storage, reporting, detection, ml} -> domain

Imports under `if TYPE_CHECKING:` (type hints only) and the `__main__.py` entry shims are exempt.
"""
import ast
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[2] / "notam_schedule"
ROOT = "notam_schedule"

ALLOWED = {
    "domain": {"domain"},
    "detection": {"domain", "detection"},
    "ingestion": {"domain", "ingestion", "config"},
    "storage": {"domain", "storage", "config"},
    "reporting": {"domain", "reporting"},
    "ml": {"domain", "detection", "ml", "config"},
    "pipelines": {"domain", "detection", "ingestion", "storage", "reporting", "ml", "pipelines", "config"},
    "cli": {"domain", "detection", "ingestion", "storage", "reporting", "ml", "pipelines", "cli", "config"},
    "api": {"domain", "detection", "ingestion", "storage", "reporting", "ml", "pipelines", "api", "config"},
    "config": set(),
    "resources": set(),                  # package data only (airports.csv)
}


def _is_type_checking(node: ast.If) -> bool:
    t = node.test
    return (isinstance(t, ast.Name) and t.id == "TYPE_CHECKING") or \
        (isinstance(t, ast.Attribute) and t.attr == "TYPE_CHECKING")


def _imports(path: Path):
    """(module name, line) of every runtime import, with relative imports resolved."""
    rel = path.relative_to(PKG.parent).with_suffix("")
    package = list(rel.parts[:-1])  # the package that relative imports start from
    skip: set[int] = set()
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _is_type_checking(node):
            skip.update(id(n) for n in ast.walk(node))
    for node in ast.walk(tree):
        if id(node) in skip:
            continue
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name, node.lineno
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - node.level + 1]
                mod = ".".join(base + ([node.module] if node.module else []))
                if not node.module:  # "from . import X" / "from .. import X"
                    for a in node.names:
                        yield f"{mod}.{a.name}", node.lineno
                    continue
                yield mod, node.lineno
            else:
                yield node.module, node.lineno


def _layer(module: str) -> str | None:
    parts = module.split(".")
    if parts[0] != ROOT or len(parts) < 2:
        return None  # third-party, stdlib or the package root (__version__)
    return parts[1]


def test_layers_only_import_allowed_layers():
    violations = []
    for path in sorted(PKG.rglob("*.py")):
        rel = path.relative_to(PKG)
        if path.name == "__main__.py" or rel == Path("__init__.py"):
            continue
        layer = rel.parts[0] if len(rel.parts) > 1 else rel.stem
        allowed = ALLOWED.get(layer)
        assert allowed is not None, f"{rel}: module outside the known layers {sorted(ALLOWED)}"
        for mod, line in _imports(path):
            target = _layer(mod)
            if target is not None and target != layer and target not in allowed and target != "__version__":
                violations.append(f"{rel}:{line} ({layer}) imports {mod} ({target})")
    assert not violations, "Layer violations:\n" + "\n".join(violations)


def test_domain_uses_only_the_standard_library():
    """The domain must stay free of I/O libraries (requests, openpyxl, sklearn...)."""
    bad = []
    for path in sorted((PKG / "domain").rglob("*.py")):
        for mod, line in _imports(path):
            top = mod.split(".")[0]
            if top not in (ROOT, "__future__") and top not in sys.stdlib_module_names:
                bad.append(f"{path.relative_to(PKG)}:{line} imports {mod}")
    assert not bad, "\n".join(bad)
