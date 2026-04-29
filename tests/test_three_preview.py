import unittest

from openbrep.gdl_previewer import Preview3DResult, PreviewMesh3D
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
                )
            ],
            wires=[[(0.0, 0.0, 0.0), (1.0, 1.0, 1.0)]],
        )

        payload = preview_3d_to_three_payload(data)

        self.assertEqual(payload["meshes"][0]["name"], "block")
        self.assertEqual(len(payload["meshes"][0]["vertices"]), 3)
        self.assertEqual(payload["meshes"][0]["faces"], [[0, 1, 2]])
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
                )
            ],
        )

        html = render_three_preview_html(data)

        self.assertIn("three.module.js", html)
        self.assertIn("OrbitControls.js", html)
        self.assertIn('"name":"mesh-with-quote"', html)
        self.assertIn("new THREE.WebGLRenderer", html)


if __name__ == "__main__":
    unittest.main()
