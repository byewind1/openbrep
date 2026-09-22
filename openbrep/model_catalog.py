"""Read-only model catalog: identities, capability facts, and one strict resolver.

R1 of the model-selection plan. Design borrowed from oh-my-pi's catalog/resolver
split (MIT), reimplemented against OpenBrep's own provider registry rather than
ported.

Scope discipline:

- This module is a **projection**, never a second source of truth. Provider
  identity stays in ``openbrep/config.py`` (``PROVIDER_PROFILES``,
  ``model_to_provider``, ``normalize_provider_entry``); credentials stay in
  ``LLMConfig.resolve_credentials``. Nothing here resolves secrets, probes
  networks, contacts ``cc-switch``, or writes files.
- Production code does not consume it yet. Capability/compat migration (R2) and
  settings/session adoption (R3) land separately, each behind its own
  verification gate.
- Identity is ``provider`` + ``model_id``, but ``reference`` remains the exact
  selector string OpenBrep already stores and displays (``glm-4-flash``,
  ``gemini/gemini-2.5-flash``, ``openai-codex/gpt-5.6-luna``). Inventing a new
  canonical spelling would desynchronise config, UI, and existing tests.
- Resolution is strict: exact match only, and a selector claimed by two
  *different* models within one source is an explicit ambiguity error. Today's
  ``find_custom_provider_match`` instead falls back to configuration order for
  colliding bare ids; that behaviour is deliberately not reproduced here.
- Cross-source collisions are precedence, not ambiguity: a configured provider
  entry shadows the built-in preset it overrides, which is what today's read
  path and the settings payload already do.

Capability values are three-state. ``unsupported`` is asserted only where the
current pipeline demonstrably treats a model that way (absence from
``VISION_MODELS`` / ``REASONING_MODELS`` for built-in presets); custom and
dynamically discovered models stay ``unknown`` until a caller supplies evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Iterable, Literal, Mapping, Sequence

from openbrep.config import (
    ALL_MODELS,
    API_MODE_CODEX_APP_SERVER,
    CODEX_PROVIDER_NAME,
    REASONING_MODELS,
    VISION_MODELS,
    is_codex_qualified_model,
    iter_custom_provider_model_entries,
    normalize_provider_entry,
    provider_profile_for_model,
)

CapabilityState = Literal["supported", "unsupported", "unknown"]
ModelKind = Literal["chat", "codex"]
CatalogSource = Literal["builtin", "config", "codex"]

SUPPORTED: CapabilityState = "supported"
UNSUPPORTED: CapabilityState = "unsupported"
UNKNOWN: CapabilityState = "unknown"

# Merge precedence, highest first: configured providers shadow Codex entries,
# which shadow built-in presets. Mirrors the existing read path.
_SOURCE_RANK: dict[str, int] = {"config": 0, "codex": 1, "builtin": 2}
_SORTED_SOURCES: tuple[str, ...] = ("builtin", "config", "codex")


class ModelResolutionError(Exception):
    """A model reference did not resolve to exactly one catalog entry."""

    def __init__(self, code: str, message: str, reference: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.reference = reference


@dataclass(frozen=True)
class ModelIdentity:
    """Stable identity of one selectable model.

    ``reference`` is the string configuration, the settings payload, and the
    session override already use; callers must round-trip it unchanged.
    """

    provider: str
    model_id: str
    reference: str

    @property
    def qualified_id(self) -> str:
        return f"{self.provider}/{self.model_id}"


@dataclass(frozen=True)
class ModelCapabilities:
    """What the pipeline is known to support; ``unknown`` means unproven."""

    vision: CapabilityState = UNKNOWN
    reasoning: CapabilityState = UNKNOWN
    tools: CapabilityState = UNKNOWN

    def as_dict(self) -> dict[str, str]:
        return {"vision": self.vision, "reasoning": self.reasoning, "tools": self.tools}


@dataclass(frozen=True)
class ModelSpec:
    """One catalog entry: identity plus the facts needed to route a request."""

    identity: ModelIdentity
    display_name: str
    api_mode: str
    kind: ModelKind
    capabilities: ModelCapabilities
    source: CatalogSource

    @property
    def reference(self) -> str:
        return self.identity.reference

    @property
    def provider(self) -> str:
        return self.identity.provider


@dataclass(frozen=True)
class _Entry:
    """One published selector set pointing at one spec."""

    spec: ModelSpec
    selectors: tuple[str, ...]


@dataclass(frozen=True)
class ModelCatalog:
    """Immutable catalog with exactly one strict resolver.

    ``specs`` holds every reachable entry (a preset fully shadowed by a
    configured provider is dropped, matching the settings payload). ``selectors``
    maps a lowercased selector to its claimants: one spec on success, several
    only for a genuine within-source collision.
    """

    specs: tuple[ModelSpec, ...]
    selectors: Mapping[str, tuple[ModelSpec, ...]] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def resolve(self, reference: str) -> ModelSpec:
        """Return the single spec for ``reference``; raise on unknown/ambiguous.

        Matching is exact and case-insensitive against the selectors a spec
        publishes. There is no prefix, substring, or fuzzy fallback: an
        unrecognised reference fails instead of silently selecting a different
        provider, endpoint, or credential.
        """

        target = str(reference or "").strip()
        if not target:
            raise ModelResolutionError("model_reference_required", "Model reference is required.")
        claimants = self.selectors.get(target.lower(), ())
        if len(claimants) > 1:
            raise ModelResolutionError(
                "ambiguous_model_reference",
                f"模型引用 {target!r} 对应多个条目；请写出 provider/model 全名。",
                target,
            )
        if not claimants:
            raise ModelResolutionError("unknown_model", f"未知模型引用 {target!r}。", target)
        return claimants[0]

    def by_source(self, source: CatalogSource) -> tuple[ModelSpec, ...]:
        """Reachable entries from one source, in catalog order."""

        return tuple(spec for spec in self.specs if spec.source == source)


def build_model_catalog(
    config,
    *,
    codex_models: Iterable[Mapping[str, object]] = (),
) -> ModelCatalog:
    """Project current configuration into an immutable catalog.

    ``codex_models`` is the account catalogue the caller already holds; it is
    passed in rather than fetched, so this function performs no RPC, no login,
    and no network access. An empty catalogue is valid — it only means the Codex
    source contributes nothing.
    """

    entries: list[_Entry] = [
        *_config_entries(config),
        *_codex_entries(codex_models),
        *_builtin_entries(),
    ]
    selectors = _merge_selectors(entries)
    reachable = {spec for claimants in selectors.values() for spec in claimants}
    ordered = sorted(
        reachable,
        key=lambda spec: (
            _SORTED_SOURCES.index(spec.source),
            spec.identity.provider,
            spec.reference,
        ),
    )
    return ModelCatalog(specs=tuple(ordered), selectors=MappingProxyType(selectors))


def _merge_selectors(entries: Sequence[_Entry]) -> dict[str, tuple[ModelSpec, ...]]:
    """Fold every published selector into its claimant spec(s).

    Same source, different models -> keep both, so ``resolve`` reports
    ambiguity. Different sources -> keep the higher-precedence one.
    """

    grouped: dict[str, list[_Entry]] = {}
    for entry in entries:
        for selector in entry.selectors:
            grouped.setdefault(selector.lower(), []).append(entry)

    merged: dict[str, tuple[ModelSpec, ...]] = {}
    for selector, claimants in grouped.items():
        distinct = _distinct_entries(claimants)
        if len(distinct) == 1:
            merged[selector] = (distinct[0].spec,)
            continue
        best_rank = min(_SOURCE_RANK[entry.spec.source] for entry in distinct)
        winners = [entry for entry in distinct if _SOURCE_RANK[entry.spec.source] == best_rank]
        if len(winners) > 1:
            merged[selector] = tuple(entry.spec for entry in winners)
        else:
            merged[selector] = (winners[0].spec,)
    return merged


def _distinct_entries(claimants: Sequence[_Entry]) -> list[_Entry]:
    seen: list[_Entry] = []
    for entry in claimants:
        if all(entry.spec != other.spec for other in seen):
            seen.append(entry)
    return seen


def _builtin_entries() -> list[_Entry]:
    entries: list[_Entry] = []
    for reference in ALL_MODELS:
        profile = provider_profile_for_model(reference)
        provider = profile.name if profile is not None else "custom"
        model_id = reference
        if profile is not None and profile.native_prefix and "/" in reference:
            head, _, tail = reference.partition("/")
            if head.lower() == profile.native_prefix.rstrip("/").lower():
                model_id = tail
        spec = ModelSpec(
            identity=ModelIdentity(provider=provider, model_id=model_id, reference=reference),
            display_name=reference,
            api_mode="anthropic_messages" if provider == "anthropic" else "chat_completions",
            kind="chat",
            capabilities=ModelCapabilities(
                vision=SUPPORTED if reference in VISION_MODELS else UNSUPPORTED,
                reasoning=SUPPORTED if reference in REASONING_MODELS else UNSUPPORTED,
                # No existing fact table describes tool support; R2 derives it
                # from real call sites instead of guessing here.
                tools=UNKNOWN,
            ),
            source="builtin",
        )
        # Only the preset string itself is published: bare upstream ids such as
        # "qwen2.5:14b" are not selectable today, so they are not claimed here.
        entries.append(_Entry(spec=spec, selectors=(reference,)))
    return entries


def _config_entries(config) -> list[_Entry]:
    llm = getattr(config, "llm", config)
    entries: list[_Entry] = []
    for raw_provider in list(getattr(llm, "custom_providers", None) or []):
        if not isinstance(raw_provider, Mapping):
            continue
        provider = normalize_provider_entry(dict(raw_provider))
        name = provider["name"]
        if not name or name == CODEX_PROVIDER_NAME:
            # Reserved Codex identity is never a generic config provider.
            continue
        models = iter_custom_provider_model_entries(provider)
        if not models:
            continue
        api_mode = str(provider.get("api_mode") or "chat_completions")
        kind: ModelKind = "codex" if api_mode == API_MODE_CODEX_APP_SERVER else "chat"
        default_model = str(provider.get("default_model") or "").strip()
        default_lower = default_model.lower()
        alias_lowers = {entry["alias"].lower() for entry in models}

        for entry in models:
            alias = entry["alias"]
            target = entry["model"]
            # Documented behaviour of find_custom_provider_match: an entry
            # answers to its alias, its upstream model id, the provider-qualified
            # form of either, and — when it is the provider's default model — the
            # provider name itself.
            selectors = [alias, target, f"{name}/{alias}", f"{name}/{target}"]
            if default_lower and alias.lower() == default_lower:
                selectors.append(name)
            spec = ModelSpec(
                identity=ModelIdentity(provider=name, model_id=target, reference=alias),
                display_name=alias,
                api_mode=api_mode,
                kind=kind,
                capabilities=ModelCapabilities(),
                source="config",
            )
            entries.append(_Entry(spec=spec, selectors=tuple(dict.fromkeys(selectors))))

        if default_lower and default_lower not in alias_lowers:
            # A default_model naming an upstream id no alias lists still answers
            # to the provider name (again mirroring _match_within_provider).
            spec = ModelSpec(
                identity=ModelIdentity(
                    provider=name, model_id=default_model, reference=default_model
                ),
                display_name=default_model,
                api_mode=api_mode,
                kind=kind,
                capabilities=ModelCapabilities(),
                source="config",
            )
            entries.append(
                _Entry(
                    spec=spec,
                    selectors=(default_model, name, f"{name}/{default_model}"),
                )
            )
    return entries


def _codex_entries(codex_models: Iterable[Mapping[str, object]]) -> list[_Entry]:
    entries: list[_Entry] = []
    for raw in codex_models or []:
        if not isinstance(raw, Mapping):
            continue
        raw_id = str(raw.get("id") or raw.get("model") or "").strip()
        if not raw_id:
            continue
        if is_codex_qualified_model(raw_id) and "/" in raw_id:
            model_id = raw_id.split("/", 1)[1]
            reference = raw_id
        else:
            model_id = raw_id
            reference = f"{CODEX_PROVIDER_NAME}/{model_id}"
        label = str(raw.get("display_name") or raw.get("label") or model_id).strip() or model_id
        efforts = [
            str(item.get("effort") or "")
            for item in raw.get("supported_reasoning_efforts") or []
            if isinstance(item, Mapping)
        ]
        spec = ModelSpec(
            identity=ModelIdentity(
                provider=CODEX_PROVIDER_NAME, model_id=model_id, reference=reference
            ),
            display_name=label,
            api_mode=API_MODE_CODEX_APP_SERVER,
            kind="codex",
            capabilities=ModelCapabilities(
                reasoning=SUPPORTED if any(efforts) else UNKNOWN,
                # The account catalogue carries no modality fact, and no tool
                # fact has been derived yet: both stay unproven.
                vision=UNKNOWN,
                tools=UNKNOWN,
            ),
            source="codex",
        )
        # Codex identity is provider-qualified only; a bare model id must never
        # route to the subscription path.
        entries.append(_Entry(spec=spec, selectors=(reference,)))
    return entries
