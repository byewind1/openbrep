"""Evidence-preserving fusion of per-image and explicit user observations."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from openbrep.contracts.object_spec import Observation, ObservationItem


@dataclass(frozen=True)
class FusedObservation:
    observation: Observation
    by_state: dict[str, Observation]
    conflicts: tuple[dict, ...] = ()
    needs_clarification: tuple[str, ...] = ()


def fuse_observations(
    observations: Iterable[Observation],
    *,
    roles: list[str] | None = None,
    state_keys: list[str] | None = None,
    explicit_observation: Observation | None = None,
    explicit_by_state: dict[str, Observation] | None = None,
) -> FusedObservation:
    """Combine repeated facts without erasing provenance or inventing certainty.

    Equal known values combine. Unknown values never erase known values. Conflicting
    known values become an unknown field plus a clarification record. Explicit
    user-typed/manual facts take precedence. Distinct state groups are maintained
    separately (for example an open and a closed view).
    """
    rows = list(observations)
    role_values = list(roles or [])
    states = list(state_keys or [])
    if role_values and len(role_values) != len(rows):
        raise ValueError("roles must align one-to-one with observations")
    if states and len(states) != len(rows):
        raise ValueError("state_keys must align one-to-one with observations")
    if explicit_observation is not None and explicit_observation.source not in {"user_typed", "manual"}:
        raise ValueError("explicit_observation must be user_typed or manual")
    if explicit_by_state is not None and any(
        observation.source not in {"user_typed", "manual"}
        for observation in explicit_by_state.values()
    ):
        raise ValueError("explicit_by_state values must be user_typed or manual")

    grouped: dict[str, list[tuple[Observation, str]]] = {}
    for index, observation in enumerate(rows):
        state = str(states[index] or "default") if states else "default"
        role = str(role_values[index] or "auto") if role_values else "auto"
        grouped.setdefault(state, []).append((observation, role))
    if not grouped:
        grouped["default"] = []

    shared_explicit_by_path = {
        item.field_path: item for item in (explicit_observation.items if explicit_observation else [])
        if item.status in {"observed", "inferred"} and item.value is not None
    }
    by_state: dict[str, Observation] = {}
    conflicts: list[dict] = []
    clarify: list[str] = []
    all_refs: list[str] = []
    for observation in rows:
        all_refs.extend(observation.source_refs)
    if explicit_observation:
        all_refs.extend(explicit_observation.source_refs)
    if explicit_by_state:
        for observation in explicit_by_state.values():
            all_refs.extend(observation.source_refs)
    all_refs = list(dict.fromkeys(all_refs))

    for state, group in grouped.items():
        explicit = explicit_by_state.get(state) if explicit_by_state else None
        explicit_by_path = dict(shared_explicit_by_path)
        if explicit is not None:
            explicit_by_path.update({
                item.field_path: item for item in explicit.items
                if item.status in {"observed", "inferred"} and item.value is not None
            })
        values_by_path: dict[str, list[tuple[ObservationItem, str, Observation]]] = {}
        for observation, role in group:
            for item in observation.items:
                values_by_path.setdefault(item.field_path, []).append((item, role, observation))
        for path in explicit_by_path:
            values_by_path.setdefault(path, [])

        fused_items: list[ObservationItem] = []
        for path, candidates in values_by_path.items():
            explicit_item = explicit_by_path.get(path)
            if explicit_item is not None:
                fused_items.append(ObservationItem(
                    field_path=path, status=explicit_item.status, value=explicit_item.value, unit=explicit_item.unit,
                    confidence="high", note="explicit user value",
                ))
                continue
            known = [(item, role, source) for item, role, source in candidates
                     if item.status in {"observed", "inferred"} and item.value is not None]
            if not known:
                fused_items.append(ObservationItem(
                    field_path=path, status="unknown", value=None,
                    confidence="unknown", note="no image supplied an observable value",
                ))
                continue
            distinct: list[tuple[object, str | None]] = []
            for item, _, _ in known:
                value = (item.value, item.unit)
                if value not in distinct:
                    distinct.append(value)
            if len(distinct) > 1:
                conflict = {
                    "field_path": path,
                    "state_key": state,
                    "candidates": [
                        {
                            "value": item.value,
                            "unit": item.unit,
                            "role": role,
                            "status": item.status,
                            "confidence": item.confidence,
                            "evidence": item.note,
                            "source_refs": list(source.source_refs),
                        }
                        for item, role, source in known
                    ],
                }
                conflicts.append(conflict)
                clarify.append(path)
                fused_items.append(ObservationItem(
                    field_path=path, status="unknown", value=None,
                    confidence="unknown", note="conflicting image observations; user clarification required",
                ))
                continue
            item = known[0][0]
            status = "observed" if any(candidate.status == "observed" for candidate, _, _ in known) else "inferred"
            confidence = "high" if all(candidate.confidence == "high" for candidate, _, _ in known) else (
                "low" if any(candidate.confidence == "low" for candidate, _, _ in known) else "unknown"
            )
            refs = list(dict.fromkeys(ref for _, _, source in known for ref in source.source_refs))
            evidence = list(dict.fromkeys(item.note for item, _, _ in known if item.note))
            note = f"corroborated by {len(known)} observation(s): {', '.join(refs)}".rstrip()
            if evidence:
                note = f"{note}; evidence: {' | '.join(evidence)}"
            fused_items.append(ObservationItem(
                field_path=path, status=status, value=item.value, unit=item.unit,
                confidence=confidence, note=note,
            ))
        state_refs = list(dict.fromkeys(
            [ref for observation, _ in group for ref in observation.source_refs]
            + (list(explicit.source_refs) if explicit is not None else [])
        ))
        by_state[state] = Observation(
            observation_id=f"fused-{state}", source="vision_fusion",
            source_refs=state_refs, items=fused_items,
        )

    if len(by_state) == 1:
        combined = next(iter(by_state.values()))
    else:
        # A single flattened observation cannot faithfully encode the same field
        # in different states. Keep it empty and require consumers to use by_state.
        combined = Observation("fused-multi-state", "vision_fusion", all_refs, [])
    return FusedObservation(combined, by_state, tuple(conflicts), tuple(dict.fromkeys(clarify)))
