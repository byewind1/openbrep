"""Explicit project contracts evaluated from existing runtime evidence."""

from openbrep.contracts.object_spec import (
    ContractError,
    ExecutionPlan,
    ObjectSpec,
    Observation,
    ParseResult,
    PlanRequirementMapping,
    PlanStep,
    Relation,
    Requirement,
    known_check_executor,
    load_synthetic_minimal_contract,
    parse_execution_plan,
    parse_object_spec,
    parse_observation,
    register_check_executor,
)
from openbrep.contracts.requirement_execution import (
    RequirementEvaluation,
    execute_requirements,
    register_requirement_executor,
)
from openbrep.contracts.stair import (
    StairContractCheck,
    StairContractReport,
    evaluate_stair_contract,
)

__all__ = [
    "StairContractCheck",
    "StairContractReport",
    "evaluate_stair_contract",
    "RequirementEvaluation",
    "execute_requirements",
    "register_requirement_executor",
    # U03-A 对象合同（Observation / ObjectSpec / ExecutionPlan）
    "ContractError",
    "ExecutionPlan",
    "Observation",
    "ObjectSpec",
    "ParseResult",
    "PlanRequirementMapping",
    "PlanStep",
    "Relation",
    "Requirement",
    "known_check_executor",
    "load_synthetic_minimal_contract",
    "parse_execution_plan",
    "parse_object_spec",
    "parse_observation",
    "register_check_executor",
]
