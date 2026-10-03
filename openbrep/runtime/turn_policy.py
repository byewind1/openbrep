"""Permission policy for one conversation turn, independent of UI/session state."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Callable, Literal

Mode = Literal['consult', 'plan', 'execute']
_INTENTS = {'CHAT', 'CREATE', 'MODIFY', 'DEBUG', 'REPAIR', 'IMAGE'}
_PLAN = re.compile(r'先别改|先不要改|先出(?:个)?计划|出(?:个)?计划|给我(?:个|一个)?方案|建模方案|别动手|\b(?:plan first|plan only|only plan)\b', re.I)
_CONSULT = re.compile(r'只解释|只分析|只讨论|解释|有什么思路|优缺点|讲讲|如何解析|是什么意思|先别生成|\b(?:explain|what are|what happens|only discuss)\b', re.I)
_NEGATIVE = re.compile(r'(?:不要|别|不用|不需(?:要)?|不想|暂不|不把|不改|不修改|保持不变|without\s+(?:fixing|changing|editing)|do\s+not|don[’\x27]t|never)', re.I)
_EXECUTE = re.compile(r'直接帮我|(?:把|将)\s*[^，。；\n]{1,70}(?:改成|改为|设为|设置为|增加|减少)|(?:给|为)[^，。；\n]{1,50}(?:加|添加)|请[^，。；\n]{0,20}(?:修复|修改|添加|删除|生成)|^(?:打开|关闭|启用|禁用|增加|添加|删除|修改|修复|优化|生成|创建|新建)|\b(?:set|change|update|increase|decrease|enable|disable|generate|create|add|fix)\b', re.I)
_CREATE = re.compile(r'生成|创建|新建|\b(?:generate|create)\b', re.I)
_REFERENCE = re.compile(r'按.*(?:方案|计划).*做|执行.*(?:方案|计划)|\b(?:implement|execute)\b', re.I)
_HYPOTHETICAL = re.compile(r'^(?:例如|假如|假设|如果|if\b|for example\b|suppose\b)', re.I)


@dataclass(frozen=True)
class TurnPolicy:
    mode: Mode
    task_intent: str = 'CHAT'
    constraints: tuple[str, ...] = ()
    negated_clauses: tuple[str, ...] = ()
    decision_source: str = 'rule'
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def explicit_policy(message: str, requested_mode: str = 'auto') -> TurnPolicy | None:
    """Only high precision rules; None asks the caller for semantic judgement."""
    if requested_mode not in {'auto', 'consult', 'plan'}:
        raise ValueError('Invalid requested_mode')
    text = message.strip()
    command_text = re.sub(r'```[\s\S]*?```|“[^”]*”|「[^」]*」|"[^"\n]*"|`[^`]*`', '', text)
    clauses = tuple(c.strip() for c in re.split(r'[，。；\n,;]', command_text) if c.strip())
    negatives = tuple(c for c in clauses if _NEGATIVE.search(c))
    positives = tuple(c for c in clauses if not _NEGATIVE.search(c) and _EXECUTE.search(c))
    positives += tuple(c for c in clauses if c not in positives and not _NEGATIVE.search(c) and _CREATE.search(c))
    intent = 'CREATE' if _CREATE.search(' '.join(positives)) else 'MODIFY'
    # Quoted/hypothetical examples do not authorize mutations.
    if _HYPOTHETICAL.search(text) or _CONSULT.search(text):
        # "先别改，看看怎么优化" explicitly requests a plan, not explanation.
        mode = 'plan' if _PLAN.search(text) and not re.search(r'只解释|只分析|only explain', text, re.I) else 'consult'
        if requested_mode == 'consult':
            mode = 'consult'
        return TurnPolicy(mode, 'CHAT', negatives, negatives)
    if requested_mode == 'consult':
        return TurnPolicy('consult', 'CHAT', negatives, negatives)
    if _PLAN.search(text) or requested_mode == 'plan':
        if negatives and not positives and not _PLAN.search(text):
            return TurnPolicy('consult', 'CHAT', negatives, negatives)
        return TurnPolicy('plan', intent, negatives, negatives)
    if positives:
        return TurnPolicy('execute', intent, negatives, negatives)
    if negatives:
        return TurnPolicy('consult', 'CHAT', negatives, negatives)
    if command_text != text and not positives:
        return TurnPolicy('consult')
    if not text or re.fullmatch(r'你好[！!。\s]*|hello[!.\s]*|hi[!.\s]*', text, re.I):
        return TurnPolicy('consult')
    return None


def decide_turn(
    message: str, *, requested_mode: str = 'auto', history: list[dict] | None = None,
    working_intent: dict | None = None, project_state: dict | None = None,
    semantic_decision: Callable[[dict], dict | str] | None = None,
) -> TurnPolicy:
    """At most one semantic call; unavailable/invalid decisions never authorize writes."""
    policy = explicit_policy(message, requested_mode)
    state = project_state or {}
    if policy is None and _REFERENCE.search(message):
        if (working_intent or {}).get('proposals') or (working_intent or {}).get('pending_plan'):
            policy = TurnPolicy('execute', 'MODIFY')
    if policy is None:
        if semantic_decision is None:
            return TurnPolicy('consult', decision_source='unavailable', error='DECISION_UNAVAILABLE')
        payload = dict(message=message, history=history or [], working_intent=working_intent or {}, project_state=state)
        try:
            decision = semantic_decision(payload)
            if isinstance(decision, str):
                decision = json.loads(decision)
            if not isinstance(decision, dict) or decision.get('mode') not in {'consult', 'plan', 'execute'}:
                raise ValueError('Invalid semantic mode')
            intent = decision.get('task_intent', 'CHAT')
            constraints = decision.get('constraints', [])
            if intent not in _INTENTS or not isinstance(constraints, list) or not all(isinstance(c, str) for c in constraints):
                raise ValueError('Invalid semantic policy')
            policy = TurnPolicy(decision['mode'], intent, tuple(constraints), decision_source='semantic')
        except Exception:
            return TurnPolicy('consult', decision_source='failed', error='DECISION_FAILED')
    if policy.mode == 'execute' and policy.task_intent != 'CREATE' and not state.get('has_project', True):
        return TurnPolicy('consult', policy.task_intent, policy.constraints, policy.negated_clauses,
                          policy.decision_source, 'MISSING_TARGET')
    return policy
