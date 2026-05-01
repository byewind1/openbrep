import unittest

from openbrep.gdl_previewer import Preview3DResult, PreviewMesh3D, PreviewSourceRef
from ui.three_preview import preview_3d_to_three_payload, render_three_preview_html


class TestThreePreview(unittest.TestCase):
    def test_payload_converts_meshes_and_filters_invalid_faces(self):
        data = Preview3DResult(
            meshes=[
                PreviewMesh3D(
                    name="block",
                    x=[0.0, 1.0, 0.0],
                    y=[0.0, 0.0, 1.0],
                    z=[0.0, 0.0, 0.0],
                    i=[0, 0],
                    j=[1, 2],
                    k=[2, 9],
                    source_ref=PreviewSourceRef(
                        script_type="3d",
                        line=7,
                        command="BLOCK",
                        label="3D line 7 BLOCK",
                    ),
                )
            ],
            wires=[[(0.0, 0.0, 0.0), (1.0, 1.0, 1.0)]],
        )

        payload = preview_3d_to_three_payload(data)

        self.assertEqual(payload["meshes"][0]["name"], "block")
        self.assertEqual(len(payload["meshes"][0]["vertices"]), 3)
        self.assertEqual(payload["meshes"][0]["faces"], [[0, 1, 2]])
        self.assertEqual(
            payload["meshes"][0]["source_ref"],
            {"script_type": "3d", "line": 7, "command": "BLOCK", "label": "3D line 7 BLOCK"},
        )
        self.assertEqual(payload["wires"], [[[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]]])

    def test_html_contains_three_imports_and_payload(self):
        data = Preview3DResult(
            meshes=[
                PreviewMesh3D(
                    name="mesh-with-quote",
                    x=[0.0, 1.0, 0.0],
                    y=[0.0, 0.0, 1.0],
                    z=[0.0, 0.0, 0.0],
                    i=[0],
                    j=[1],
                    k=[2],
                    source_ref=PreviewSourceRef(
                        script_type="3d",
                        line=11,
                        command="BLOCK",
                        label="3D line 11 BLOCK",
                    ),
                )
            ],
        )

        html = render_three_preview_html(data)

        self.assertIn("three.module.js", html)
        self.assertIn("OrbitControls.js", html)
        self.assertIn('"name":"mesh-with-quote"', html)
        self.assertIn("new THREE.WebGLRenderer", html)
        self.assertIn("requestFullscreen", html)
        self.assertIn("embedded-fullscreen", html)
        self.assertIn('id="inspector"', html)
        self.assertIn("new THREE.Raycaster", html)
        self.assertIn("pickSolid", html)
        self.assertIn("sourceRefText", html)
        self.assertIn("solid.userData.sourceRef", html)
        self.assertIn('"source_ref":{"script_type":"3d","line":11,"command":"BLOCK","label":"3D line 11 BLOCK"}', html)


if __name__ == "__main__":
    unittest.main()
