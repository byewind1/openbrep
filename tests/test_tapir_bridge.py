from __future__ import annotations

from types import SimpleNamespace

import openbrep.tapir_bridge as tapir_bridge_module
from openbrep.tapir_bridge import TapirBridge


def test_addon_command_parameters_are_passed_as_raw_mapping():
    calls = []

    class FakeCommands:
        def ExecuteAddOnCommand(self, command_id, parameters):
            calls.append((command_id, parameters))
            return {"success": True}

    bridge = TapirBridge()
    bridge._conn = SimpleNamespace(
        types=SimpleNamespace(
            AddOnCommandId=lambda namespace, name: (namespace, name),
        ),
        commands=FakeCommands(),
    )

    result = bridge._tapir_call(
        "EvaluateLibraryPart",
        {"libPartName": "Chair", "parameters": {"A": 1.25}},
        addon_id="OpenBrep",
    )

    assert result == {"success": True}
    assert calls == [
        (
            ("OpenBrep", "EvaluateLibraryPart"),
            {"libPartName": "Chair", "parameters": {"A": 1.25}},
        )
    ]


def test_evaluate_library_part_forwards_requested_outputs():
    calls = []

    class FakeCommands:
        def ExecuteAddOnCommand(self, command_id, parameters):
            calls.append(parameters)
            return {"success": True}

    bridge = TapirBridge()
    bridge._conn = SimpleNamespace(
        types=SimpleNamespace(AddOnCommandId=lambda namespace, name: (namespace, name)),
        commands=FakeCommands(),
    )

    bridge.evaluate_library_part(
        lib_part_name="Stair",
        parameters={"A": 4.6},
        want=["mesh3d", "prims2d"],
    )

    assert calls == [{
        "libPartName": "Stair",
        "parameters": {"A": 4.6},
        "want": ["mesh3d", "prims2d"],
    }]


def test_connect_scans_archicad_ports_instead_of_consuming_workbench_port(monkeypatch):
    calls = []
    connection = object()

    class FakeConnectionApi:
        @staticmethod
        def find_first_port():
            calls.append(("scan",))
            return 19723

        @staticmethod
        def connect(port):
            calls.append(("connect", port))
            return connection

    monkeypatch.setattr(tapir_bridge_module, "_AC_AVAILABLE", True)
    monkeypatch.setattr(tapir_bridge_module, "ACConnection", FakeConnectionApi)

    bridge = TapirBridge()

    assert bridge.connect() is True
    assert bridge._conn is connection
    assert calls == [("scan",), ("connect", 19723)]
