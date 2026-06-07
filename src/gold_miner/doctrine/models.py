"""投资军规、策略与思维模型数据模型."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class InvestmentRule:
    """投资军规 — 不可协商的硬约束."""

    id: str
    name: str
    description: str
    severity: str  # block / warn / info
    category: str  # position_sizing / timing / emotion / process
    check_fn: str  # checker 方法名
    enabled: bool = True


@dataclass
class RuleViolation:
    """规则检查结果."""

    rule: InvestmentRule
    passed: bool
    message: str
    details: dict[str, Any] | None = None


@dataclass
class InvestmentStrategy:
    """投资策略模板."""

    id: str
    name: str
    description: str
    applicable_regime: str  # trending / ranging / crisis / recovery / all
    position_sizing: str = ""
    entry_rules: list[str] = field(default_factory=list)
    exit_rules: list[str] = field(default_factory=list)
    stop_loss_rule: str = ""
    mental_models: list[str] = field(default_factory=list)
    pros: list[str] = field(default_factory=list)
    cons: list[str] = field(default_factory=list)


@dataclass
class MentalModel:
    """投资思维模型."""

    id: str
    name: str
    description: str
    key_principle: str
    when_to_apply: str
    gold_application: str = ""
    reference: str = ""
    related_strategies: list[str] = field(default_factory=list)


@dataclass
class DoctrineResult:
    """军规审查完整结果."""

    violations: list[RuleViolation] = field(default_factory=list)
    blocks: list[RuleViolation] = field(default_factory=list)
    warnings: list[RuleViolation] = field(default_factory=list)
    infos: list[RuleViolation] = field(default_factory=list)
    passed_count: int = 0
    failed_count: int = 0

    @property
    def has_blocks(self) -> bool:
        return len(self.blocks) > 0

    @property
    def all_passed(self) -> bool:
        return self.failed_count == 0
