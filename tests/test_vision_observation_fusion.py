from openbrep.contracts.object_spec import Observation, ObservationItem
from openbrep.vision.observation_fusion import fuse_observations


def _observation(obs_id, items, source="vision_extraction"):
    return Observation(obs_id, source, [obs_id], [ObservationItem(**item) for item in items])


def test_equal_observations_fuse_and_keep_all_source_refs():
    fused = fuse_observations([
        _observation("img-1", [{"field_path": "overall.width", "status": "observed", "value": 1.2, "unit": "m", "confidence": "high"}]),
        _observation("img-2", [{"field_path": "overall.width", "status": "observed", "value": 1.2, "unit": "m", "confidence": "high"}]),
    ], roles=["outline", "pattern"])

    item = fused.observation.items[0]
    assert item.value == 1.2 and item.status == "observed"
    assert fused.observation.source_refs == ["img-1", "img-2"]
    assert fused.conflicts == ()


def test_conflicting_views_become_unknown_and_require_clarification():
    fused = fuse_observations([
        _observation("img-1", [{"field_path": "pattern.count", "status": "observed", "value": 3}]),
        _observation("img-2", [{"field_path": "pattern.count", "status": "observed", "value": 4}]),
    ], roles=["outline", "pattern"])

    item = fused.observation.items[0]
    assert item.status == "unknown" and item.value is None
    assert fused.needs_clarification == ("pattern.count",)
    assert [candidate["value"] for candidate in fused.conflicts[0]["candidates"]] == [3, 4]


def test_unknown_observation_never_erases_known_value_and_user_value_wins():
    known = _observation("img-1", [{"field_path": "overall.width", "status": "observed", "value": 1.2, "unit": "m"}])
    unknown = _observation("img-2", [{"field_path": "overall.width", "status": "unknown", "value": None}])
    explicit = Observation("user", "user_typed", ["instruction"], [ObservationItem("overall.width", "observed", 1.5, "m", "high")])

    without_user = fuse_observations([known, unknown])
    with_user = fuse_observations([known, unknown], explicit_observation=explicit)

    assert without_user.observation.items[0].value == 1.2
    assert with_user.observation.items[0].value == 1.5
    assert with_user.observation.items[0].note == "explicit user value"


def test_observations_from_distinct_states_are_not_compared_or_overwritten():
    fused = fuse_observations([
        _observation("open", [{"field_path": "state.opening", "status": "observed", "value": "open"}]),
        _observation("closed", [{"field_path": "state.opening", "status": "observed", "value": "closed"}]),
    ], state_keys=["open", "closed"])

    assert fused.conflicts == ()
    assert set(fused.by_state) == {"open", "closed"}


def test_user_override_is_limited_to_its_state_group():
    fused = fuse_observations(
        [
            _observation("open", [{"field_path": "parts.doors.state", "status": "observed", "value": "open"}]),
            _observation("closed", [{"field_path": "parts.doors.state", "status": "observed", "value": "closed"}]),
        ],
        state_keys=["open", "closed"],
        explicit_by_state={
            "closed": Observation(
                "user-closed", "user_typed", ["confirmation:closed"],
                [ObservationItem("parts.doors.state", "observed", "ajar", confidence="high")],
            ),
        },
    )

    assert fused.by_state["open"].items[0].value == "open"
    assert fused.by_state["closed"].items[0].value == "ajar"
    assert "confirmation:closed" in fused.by_state["closed"].source_refs
