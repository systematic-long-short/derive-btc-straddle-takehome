"""Fail-closed AST scanner for candidate submissions."""

from __future__ import annotations

import ast
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

THIRD_PARTY_ALLOWED = frozenset({
    "derivebench",
    "httpx",
    "websockets",
    "numpy",
    "pandas",
    "polars",
    "scipy",
    "statsmodels",
    "sklearn",
    "lightgbm",
    "xgboost",
    "optuna",
    "ta",
})

STDLIB_ALLOWED = frozenset({
    "__future__",
    "abc",
    "array",
    "bisect",
    "calendar",
    "cmath",
    "collections",
    "contextlib",
    "copy",
    "dataclasses",
    "datetime",
    "decimal",
    "enum",
    "fractions",
    "functools",
    "heapq",
    "itertools",
    "json",
    "logging",
    "math",
    "operator",
    "random",
    "re",
    "statistics",
    "string",
    "textwrap",
    "time",
    "typing",
    "warnings",
    "zoneinfo",
})

ALLOWED_MODULES = STDLIB_ALLOWED | THIRD_PARTY_ALLOWED
BLOCKED_MODULES = frozenset({
    "ctypes",
    "dill",
    "cloudpickle",
    "fcntl",
    "ftplib",
    "importlib",
    "inspect",
    "marshal",
    "multiprocessing",
    "os",
    "pickle",
    "pty",
    "resource",
    "shutil",
    "signal",
    "socket",
    "ssl",
    "subprocess",
    "sys",
})
BLOCKED_CALL_NAMES = frozenset({"eval", "exec", "compile", "__import__", "open", "input", "getattr", "setattr", "delattr", "globals", "locals", "vars", "breakpoint"})
BLOCKED_ATTR_NAMES = frozenset({
    "open",
    "read_text",
    "read_bytes",
    "write_text",
    "write_bytes",
    "unlink",
    "rmdir",
    "mkdir",
    "touch",
    "chmod",
    "chown",
    "home",
    "cwd",
    "resolve",
    "absolute",
    "expanduser",
    "to_csv",
    "to_json",
    "to_parquet",
    "to_pickle",
    "__bases__",
    "__base__",
    "__builtins__",
    "__class__",
    "__closure__",
    "__code__",
    "__dict__",
    "__globals__",
    "__getattribute__",
    "__mro__",
    "__reduce__",
    "__reduce_ex__",
    "__subclasses__",
})
BLOCKED_ATTR_PATHS = frozenset({
    "os.environ",
    "os.getenv",
    "os.system",
    "os.popen",
    "pathlib.Path.open",
    "pathlib.Path.read_text",
    "pathlib.Path.write_text",
    "subprocess.run",
    "subprocess.Popen",
    "pandas.read_csv",
    "pandas.read_parquet",
    "numpy.load",
    "numpy.fromfile",
})
BLOCKED_ATTR_PREFIXES = ("pandas.read_", "polars.read_", "polars.scan_")
PROTECTED_ROOTS = frozenset({"derivebench", "httpx", "websockets", "numpy", "pandas", "polars", "scipy", "statsmodels", "sklearn", "lightgbm", "xgboost", "optuna", "ta"})
DERIVEBENCH_ALLOWED_IMPORTS = frozenset({"derivebench", "derivebench.model"})
DERIVEBENCH_ROOT_NAMES = frozenset({"FLAT", "EventResult", "MarketInfo", "Model", "RunResult", "RunSegment", "Side", "Signal", "Tick"})


@dataclass(frozen=True, slots=True)
class Finding:
    severity: str
    rule: str
    line: int
    col: int
    message: str


@dataclass(frozen=True, slots=True)
class ScanReport:
    file: str
    verdict: str
    findings: tuple[Finding, ...]
    imports: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"file": self.file, "verdict": self.verdict, "imports": list(self.imports), "findings": [asdict(f) for f in self.findings]}

    def format_text(self) -> str:
        lines = [
            f"derivebench submission scan - {self.file}",
            "=" * 62,
            f"verdict: {self.verdict.upper()}",
            f"imports seen: {', '.join(self.imports) if self.imports else '(none)'}",
            "",
        ]
        lines.extend("no findings." if not self.findings else f"  [{f.severity:>8}] {f.rule} line {f.line}:{f.col} - {f.message}" for f in self.findings)
        return "\n".join(lines)


def _attr_path(node: ast.AST) -> str | None:
    parts: list[str] = []
    cur: ast.AST | None = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return ".".join(reversed(parts))
    return None


def _blocked_path(path: str) -> bool:
    return path in BLOCKED_ATTR_PATHS or any(path.startswith(prefix) for prefix in BLOCKED_ATTR_PREFIXES)


class _Visitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.findings: list[Finding] = []
        self.imports: list[str] = []
        self.aliases: dict[str, str] = {}
        self.model_submission_classes = 0

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if node.name == "ModelSubmission":
            self.model_submission_classes += 1
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._check_module(alias.name, node)
            self.aliases[alias.asname or alias.name.split(".", 1)[0]] = alias.name
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        mod = node.module or ""
        if mod:
            self._check_module(mod, node)
        for alias in node.names:
            if alias.name == "*":
                self._add(node, "critical", "star_import", "star imports are not allowed")
            if mod == "derivebench" and alias.name not in DERIVEBENCH_ROOT_NAMES:
                self._add(node, "critical", "blocked_import", f"from derivebench import {alias.name!r} is not part of the public candidate API")
            self.aliases[alias.asname or alias.name] = f"{mod}.{alias.name}" if mod else alias.name
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name):
            normalized = self._normalize(node.func.id)
            if node.func.id in BLOCKED_CALL_NAMES or _blocked_path(normalized):
                self._add(node, "critical", "blocked_call", f"call to blocked target {normalized}")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        raw = _attr_path(node)
        path = self._normalize(raw) if raw else None
        if path and _blocked_path(path):
            self._add(node, "critical", "blocked_attr", f"reference to blocked attribute {path}")
        elif node.attr in BLOCKED_ATTR_NAMES:
            self._add(node, "critical", "blocked_attr", f"reference to blocked attribute {node.attr}")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id == "__builtins__":
            self._add(node, "critical", "blocked_name", "reference to __builtins__")
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        self._check_targets(node.targets, node)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._check_targets([node.target], node)
        self.generic_visit(node)

    def _check_module(self, name: str, node: ast.AST) -> None:
        self.imports.append(name)
        root = name.split(".", 1)[0]
        if root in BLOCKED_MODULES:
            self._add(node, "critical", "blocked_import", f"import of blocked module {name!r}")
        elif root == "derivebench" and name not in DERIVEBENCH_ALLOWED_IMPORTS:
            self._add(node, "critical", "blocked_import", f"import of {name!r}; submissions may import only derivebench public model API")
        elif root not in ALLOWED_MODULES:
            self._add(node, "critical", "unknown_import", f"import of {name!r} is not allowlisted")

    def _check_targets(self, targets: Iterable[ast.expr], node: ast.AST) -> None:
        for target in targets:
            if isinstance(target, (ast.Tuple, ast.List)):
                self._check_targets(target.elts, node)
            elif isinstance(target, ast.Attribute):
                raw = _attr_path(target)
                path = self._normalize(raw) if raw else ""
                if path.split(".", 1)[0] in PROTECTED_ROOTS:
                    self._add(node, "critical", "module_mutation", f"assignment to imported module attribute {path}")

    def _normalize(self, path: str) -> str:
        head, sep, rest = path.partition(".")
        mapped = self.aliases.get(head)
        return f"{mapped}{sep}{rest}" if mapped and sep else mapped or path

    def _add(self, node: ast.AST, severity: str, rule: str, message: str) -> None:
        self.findings.append(Finding(severity, rule, getattr(node, "lineno", 0), getattr(node, "col_offset", 0), message))


def scan_source(source: str, *, path: str = "<string>") -> ScanReport:
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        return ScanReport(path, "reject", (Finding("critical", "syntax_error", exc.lineno or 0, exc.offset or 0, exc.msg),))
    visitor = _Visitor()
    visitor.visit(tree)
    if visitor.model_submission_classes != 1:
        visitor.findings.append(Finding("critical", "submission_class", 0, 0, "file must define exactly one class named ModelSubmission"))
    verdict = "reject" if any(f.severity == "critical" for f in visitor.findings) else "accept"
    return ScanReport(path, verdict, tuple(visitor.findings), tuple(visitor.imports))


def scan_file(path: Path | str) -> ScanReport:
    p = Path(path)
    return scan_source(p.read_text(), path=str(p))


def iter_critical(report: ScanReport) -> Iterable[Finding]:
    return (f for f in report.findings if f.severity == "critical")


def as_json(report: ScanReport) -> str:
    return json.dumps(report.to_dict(), indent=2)

