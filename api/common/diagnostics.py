from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping

from api.common.schema import SourceLocation


class DiagnosticSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    NOTE = "note"


@dataclass(frozen=True)
class Diagnostic:
    code: str
    severity: DiagnosticSeverity
    message: str
    location: SourceLocation | None = None
    context: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ValidationResult:
    diagnostics: tuple[Diagnostic, ...] = ()

    @property
    def ok(self) -> bool:
        return not any(
            diagnostic.severity == DiagnosticSeverity.ERROR
            for diagnostic in self.diagnostics
        )

    @property
    def errors(self) -> tuple[Diagnostic, ...]:
        return tuple(
            diagnostic
            for diagnostic in self.diagnostics
            if diagnostic.severity == DiagnosticSeverity.ERROR
        )

    def raise_for_errors(self) -> None:
        if not self.ok:
            raise VfInfoValidationError(self.errors)


class VfInfoValidationError(ValueError):
    def __init__(self, diagnostics: Iterable[Diagnostic]) -> None:
        self.diagnostics = tuple(diagnostics)
        details = "; ".join(
            f"{item.code}: {item.message}" for item in self.diagnostics
        )
        super().__init__(f"CanonicalVfInfo validation failed: {details}")
