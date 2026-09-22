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

Capabilities are three-state and every value is currently ``unknown``, including
for built-in presets. ``openbrep/config.py`` still carries ``VISION_MODELS`` /
``REASONING_MODELS``, but nothing in the repository reads them, so they cannot
justify a claim in either direction — asserting ``unsupported`` from a table
with no reader would let a future consumer refuse input that works today. The
one backed fact is Codex reasoning support, which the account catalogue's
``supported_reasoning_efforts`` states and ``WorkbenchSettingsService`` actually
enforces. R2 derives the rest from real call sites and may then use
``unsupported`` for facts with a reader.

Open families and deliberate boundaries, all required to survive the R3 migration:

- ``ollama/<tag>`` is an open set: the closed validator accepts any tag
  (``model_to_provider(model) == "ollama"``) and the adapter routes it without a
  credential, so ``resolve`` synthesises an entry for it instead of failing.
  Synthesised entries are intentionally absent from ``specs``.
- A bare provider prefix with no model id (``ollama/``) resolves to nothing. The
  validator's prefix test accepts the string, but no model exists behind it, so
  refusing it is deliberate strictness rather than an oversight.
- Built-in preset membership is exact-case, mirroring ``model in ALL_MODELS`` in
  the validator. Custom aliases and upstream ids remain case-insensitive, which
  is what ``find_custom_provider_match`` does.
- A qualified reference is matched after stripping both halves
  (``" gw /a1"`` addresses provider ``gw``), mirroring the read path. A provider
  whose *name* contains ``/`` therefore publishes no ``name/model`` spelling at
  all: splitting on the first ``/`` could never parse it back to that provider,
  so the name and the entry aliases stay addressable while the qualified form is
  not claimed. A provider with no name contributes its aliases and upstream ids
  only, since it has no addressable prefix.
- The validator treats an empty reference as the configured default model
  (``find_custom_provider_match`` falls back on a falsy target); this resolver
  always reports ``model_reference_required``. Callers must pass a real string.
- ``provider/<id>`` direct connect for an id the provider does not list is
  **not** modelled here. Today
  ``_match_within_provider(..., explicit_ref=True)`` accepts it, so R3 must keep
  accepting it wherever settings validation accepts it today; this closed
  resolver will not resolve such a reference. The same applies to the
  ``name/<id>`` spelling of a provider whose name contains ``/``.
- The bare reserved Codex identity ``openai-codex`` resolves to nothing: it names
  a provider, not a model. The validator accepts the string only because the
  reserved provider entry exists, and the adapter would dispatch it without a
  model id.
- Codex references outrank configuration: the adapter dispatches any exact-case
  ``openai-codex/...`` spelling to the subscription path, so a configured alias
  or upstream id spelled that way is dead config and publishes no selector. In
  any *other* case the adapter would fall through to the generic config path,
  but the catalog still refuses spellings of the reserved identity — a
  deliberate fail-closed narrowing of a contrived configuration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Iterable, Literal, Mapping, Sequence

from openbrep.config import (
    ALL_MODELS,
    API_MODE_CODEX_APP_SERVER,
    CODEX_PROVIDER_NAME,
    is_codex_qualified_model,
    iter_custom_provider_model_entries,
    normalize_provider_entry,
    provider_profile_for_model,
)

CapabilityState = Literal["supported", "unsupported", "unknown"]
ModelKind = Literal["chat", "codex"]
CatalogSource = Literal["builtin", "config", "codex", "open"]

SUPPORTED: CapabilityState = "supported"
UNSUPPORTED: CapabilityState = "unsupported"
UNKNOWN: CapabilityState = "unknown"

# Merge precedence, highest first: configured providers shadow Codex entries,
# which shadow built-in presets. Mirrors the existing read path.
_SOURCE_RANK: dict[str, int] = {"config": 0, "codex": 1, "builtin": 2}
_SORTED_SOURCES: tuple[str, ...] = ("builtin", "config", "codex")

# Providers whose model ids are an open set that the closed settings validator
# accepts and the adapter routes without a catalog entry: today
# ``_catalog_known_model`` treats any ``ollama/<id>`` as known (local tags come
# and go), so the resolver must not turn them into ``unknown_model``.
_OPEN_FAMILIES: tuple[tuple[str, str], ...] = (("ollama/", "ollama"),)


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

        # The read path splits a qualified reference on its first "/" and strips
        # both halves before matching, so " gw /a1" addresses provider gw. Try
        # that interpretation first, exactly as find_custom_provider_match does,
        # then fall back to the reference as written.
        lookups: list[tuple[str, str, bool]] = []
        if "/" in target:
            head, _, rest = target.partition("/")
            normalized = f"{head.strip()}/{rest.strip()}"
            lookups.append((normalized.lower(), normalized, True))
        lookups.append((target.lower(), target, False))

        claimants: tuple[ModelSpec, ...] = ()
        written = target
        for key, original, is_normalized in lookups:
            found = self.selectors.get(key, ())
            if is_normalized:
                # Only the custom-provider direct-connect parse strips inner
                # whitespace. Literal identities (preset membership, the Codex
                # prefix) are compared as written, so they are never reached
                # through a padded spelling.
                found = tuple(spec for spec in found if spec.source == "config")
            if found:
                claimants, written = found, original
                break

        if claimants:
            # Built-in presets and Codex identities are matched exact-case: the
            # validator tests ``model in ALL_MODELS`` and
            # ``is_codex_qualified_model`` / ``m["id"] == model`` literally, and
            # the adapter dispatches Codex on the exact ``openai-codex/`` prefix.
            # Custom aliases and upstream ids stay case-insensitive, mirroring
            # ``find_custom_provider_match``'s ``.lower()`` comparison.
            exact = tuple(
                spec
                for spec in claimants
                if spec.source not in ("builtin", "codex") or spec.reference == written
            )
            claimants = exact
        if len(claimants) > 1:
            raise ModelResolutionError(
                "ambiguous_model_reference",
                f"模型引用 {target!r} 对应多个条目；请写出 provider/model 全名。",
                target,
            )
        if not claimants:
            open_spec = _open_family_spec(target)
            if open_spec is not None:
                # Open families have no bounded membership, so the entry is
                # synthesised here and is deliberately absent from ``specs``.
                return open_spec
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


def _open_family_spec(reference: str) -> ModelSpec | None:
    """Synthesise an entry for a provider whose model ids are an open set."""

    lowered = reference.lower()
    for prefix, provider in _OPEN_FAMILIES:
        if lowered.startswith(prefix) and len(reference) > len(prefix):
            return ModelSpec(
                identity=ModelIdentity(
                    provider=provider,
                    model_id=reference[len(prefix):],
                    reference=reference,
                ),
                display_name=reference,
                api_mode="chat_completions",
                kind="chat",
                # A local tag states nothing about modality or tools.
                capabilities=ModelCapabilities(),
                source="open",
            )
    return None


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
            # No capability fact exists for presets: the legacy VISION_MODELS /
            # REASONING_MODELS constants have no reader in the repository, and
            # llm.py attempts image input for whatever model is configured.
            capabilities=ModelCapabilities(),
            source="builtin",
        )
        # Only the preset string itself is published: bare upstream ids such as
        # "qwen2.5:14b" are not selectable today, so they are not claimed here.
        entries.append(_Entry(spec=spec, selectors=(reference,)))
    return entries


def _config_spec(
    provider_name: str,
    alias: str,
    target: str,
    api_mode: str,
    kind: ModelKind,
) -> ModelSpec:
    return ModelSpec(
        identity=ModelIdentity(provider=provider_name, model_id=target, reference=alias),
        display_name=alias,
        api_mode=api_mode,
        kind=kind,
        capabilities=ModelCapabilities(),
        source="config",
    )


def _provider_name_target(
    name: str,
    models: Sequence[Mapping[str, str]],
    default_model: str,
) -> tuple[str, str, bool]:
    """Which model the bare provider name addresses, mirroring the read path.

    ``_match_within_provider`` resolves an empty remainder to the default model
    (by alias or upstream id), synthesises ``(default_model, default_model)``
    when that id is not listed, and otherwise falls back to the first listed
    model — then to a ``(name, name)`` synthetic entry when nothing is listed.

    The third element reports whether the pair is a listed model, which decides
    whether the bare upstream id itself is a routable reference.
    """

    if default_model:
        lowered = default_model.lower()
        for entry in models:
            if lowered in {entry["alias"].lower(), entry["model"].lower()}:
                return entry["alias"], entry["model"], True
        return default_model, default_model, False
    if models:
        return models[0]["alias"], models[0]["model"], True
    if name:
        return name, name, False
    return name, name, False


def _config_entries(config) -> list[_Entry]:
    llm = getattr(config, "llm", config)
    entries: list[_Entry] = []
    for raw_provider in list(getattr(llm, "custom_providers", None) or []):
        if not isinstance(raw_provider, Mapping):
            continue
        provider = normalize_provider_entry(dict(raw_provider))
        name = provider["name"]
        if name == CODEX_PROVIDER_NAME:
            # Reserved Codex identity is never a generic config provider.
            continue
        models = iter_custom_provider_model_entries(provider)
        api_mode = str(provider.get("api_mode") or "chat_completions")
        kind: ModelKind = "codex" if api_mode == API_MODE_CODEX_APP_SERVER else "chat"
        # Qualified references are parsed by splitting on the FIRST "/" and
        # requiring the head to equal the provider name, so a name containing
        # "/" can never be written back as name/model. Publishing such a
        # spelling would claim a reference the read path routes elsewhere.
        qualified_ok = bool(name) and "/" not in name
        model_specs: dict[str, ModelSpec] = {}

        def spec_for(alias: str, target: str) -> ModelSpec:
            """One spec per routable upstream model of this provider.

            Keying on the upstream id (not on the alias) keeps several aliases of
            one model from being reported as an ambiguity: they are the same
            routable model, and the first listed alias becomes its display
            reference while every alias stays resolvable.
            """

            key = target.lower()
            spec = model_specs.get(key)
            if spec is None:
                spec = _config_spec(name, alias, target, api_mode, kind)
                model_specs[key] = spec
            return spec

        for entry in models:
            alias = entry["alias"]
            target = entry["model"]
            # Documented behaviour of find_custom_provider_match: an entry
            # answers to its alias, its upstream model id, and — when the
            # provider name is addressable — the provider-qualified form of
            # either. Any spelling that names the reserved Codex identity is
            # dropped: the adapter dispatches every ``openai-codex/...`` string
            # to the subscription path, so a config alias spelled that way is
            # dead config, not a routable reference.
            selectors = [alias, target]
            if qualified_ok:
                selectors += [f"{name}/{alias}", f"{name}/{target}"]
            selectors = [item for item in selectors if not is_codex_qualified_model(item)]
            if not selectors:
                continue
            entries.append(
                _Entry(spec=spec_for(alias, target), selectors=tuple(dict.fromkeys(selectors)))
            )

        if not name:
            # A provider with no name has no addressable prefix; its aliases
            # still route today, so they stay published.
            continue
        default_model = str(provider.get("default_model") or "").strip()
        alias, target, listed = _provider_name_target(name, models, default_model)
        # The provider name points at the same spec as the model it addresses, so
        # naming the default model explicitly never turns into a false
        # ambiguity. An unlisted default id stays reachable through the provider
        # name and its qualified form only: the bare id has no configuration
        # entry and the adapter cannot route it.
        selectors = [name]
        if qualified_ok:
            selectors.append(f"{name}/{target}")
            if listed:
                selectors.extend([alias, target, f"{name}/{alias}"])
        selectors = [item for item in selectors if not is_codex_qualified_model(item)]
        if not selectors:
            continue
        entries.append(
            _Entry(
                spec=spec_for(alias, target),
                selectors=tuple(dict.fromkeys(selectors)),
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
                # Backed fact: the account catalogue states it and
                # WorkbenchSettingsService refuses efforts outside it.
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
