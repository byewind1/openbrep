from __future__ import annotations

import json

from openbrep.gdl_previewer import Preview3DResult


THREE_VERSION = "0.164.1"


def preview_3d_to_three_payload(data: Preview3DResult) -> dict:
    meshes = []
    for mesh in data.meshes:
        vertex_count = min(len(mesh.x), len(mesh.y), len(mesh.z))
        index_count = min(len(mesh.i), len(mesh.j), len(mesh.k))
        vertices = [[mesh.x[idx], mesh.y[idx], mesh.z[idx]] for idx in range(vertex_count)]
        faces = [
            [mesh.i[idx], mesh.j[idx], mesh.k[idx]]
            for idx in range(index_count)
            if _valid_face(mesh.i[idx], mesh.j[idx], mesh.k[idx], vertex_count)
        ]
        meshes.append({"name": mesh.name, "vertices": vertices, "faces": faces})

    wires = []
    for wire in data.wires:
        points = [[p[0], p[1], p[2]] for p in wire if len(p) == 3]
        if len(points) >= 2:
            wires.append(points)

    return {"meshes": meshes, "wires": wires}


def render_three_preview_html(data: Preview3DResult, *, height: int = 500) -> str:
    payload = preview_3d_to_three_payload(data)
    payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <script type="importmap">
    {{
      "imports": {{
        "three": "https://unpkg.com/three@{THREE_VERSION}/build/three.module.js",
        "three/addons/": "https://unpkg.com/three@{THREE_VERSION}/examples/jsm/"
      }}
    }}
  </script>
  <style>
    html, body {{
      margin: 0;
      width: 100%;
      height: {height}px;
      overflow: hidden;
      background: #f8fafc;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    #viewer {{
      width: 100%;
      height: {height}px;
      position: relative;
      border: 1px solid #e2e8f0;
      border-radius: 8px;
      background: linear-gradient(180deg, #ffffff 0%, #f1f5f9 100%);
    }}
    #viewer.embedded-fullscreen {{
      position: fixed;
      inset: 0;
      width: 100vw;
      height: 100vh;
      z-index: 999999;
      border: 0;
      border-radius: 0;
    }}
    #viewer:fullscreen {{
      width: 100vw;
      height: 100vh;
      border: 0;
      border-radius: 0;
    }}
    #toolbar {{
      position: absolute;
      top: 10px;
      right: 10px;
      display: flex;
      gap: 6px;
      z-index: 2;
    }}
    button {{
      width: 34px;
      height: 30px;
      border: 1px solid #cbd5e1;
      border-radius: 6px;
      background: rgba(255, 255, 255, 0.9);
      color: #0f172a;
      cursor: pointer;
      font-size: 13px;
      line-height: 1;
    }}
    button.active {{
      background: #0f172a;
      color: white;
      border-color: #0f172a;
    }}
    #status {{
      position: absolute;
      left: 12px;
      bottom: 10px;
      z-index: 2;
      color: #475569;
      font-size: 12px;
      pointer-events: none;
    }}
    canvas {{
      display: block;
    }}
  </style>
</head>
<body>
  <div id="viewer">
    <div id="toolbar">
      <button id="mode" title="Toggle solid and wire display">S</button>
      <button id="reset" title="Reset camera">R</button>
      <button id="fullscreen" title="Fullscreen preview">⛶</button>
    </div>
    <div id="status"></div>
  </div>
  <script type="module">
    const payload = {payload_json};
    const viewer = document.getElementById("viewer");
    const status = document.getElementById("status");
    const modeButton = document.getElementById("mode");
    const resetButton = document.getElementById("reset");
    const fullscreenButton = document.getElementById("fullscreen");

    try {{
      const THREE = await import("three");
      const controlsModule = await import("three/addons/controls/OrbitControls.js");
      const OrbitControls = controlsModule.OrbitControls;

      const renderer = new THREE.WebGLRenderer({{ antialias: true, alpha: true }});
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      renderer.setSize(viewer.clientWidth, viewer.clientHeight);
      renderer.outputColorSpace = THREE.SRGBColorSpace;
      viewer.appendChild(renderer.domElement);

      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(45, viewer.clientWidth / viewer.clientHeight, 0.001, 100000);
      const controls = new OrbitControls(camera, renderer.domElement);
      controls.enableDamping = true;
      controls.dampingFactor = 0.08;

      const root = new THREE.Group();
      scene.add(root);

      scene.add(new THREE.HemisphereLight(0xffffff, 0x94a3b8, 1.5));
      const keyLight = new THREE.DirectionalLight(0xffffff, 2.2);
      keyLight.position.set(4, -5, 8);
      scene.add(keyLight);
      const fillLight = new THREE.DirectionalLight(0xffffff, 0.7);
      fillLight.position.set(-5, 3, 5);
      scene.add(fillLight);

      const grid = new THREE.GridHelper(10, 10, 0x94a3b8, 0xdbe3ec);
      grid.rotation.x = Math.PI / 2;
      scene.add(grid);
      scene.add(new THREE.AxesHelper(2));

      const palette = [0x0ea5e9, 0x22c55e, 0xf97316, 0x8b5cf6, 0xef4444, 0x14b8a6];
      const edgeObjects = [];
      const solidObjects = [];

      payload.meshes.forEach((mesh, index) => {{
        if (!mesh.vertices.length || !mesh.faces.length) return;
        const positions = new Float32Array(mesh.vertices.flat());
        const indices = new Uint32Array(mesh.faces.flat());
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
        geometry.setIndex(new THREE.BufferAttribute(indices, 1));
        geometry.computeVertexNormals();

        const material = new THREE.MeshStandardMaterial({{
          color: palette[index % palette.length],
          roughness: 0.62,
          metalness: 0.02,
          transparent: true,
          opacity: 0.78,
          side: THREE.DoubleSide
        }});
        const solid = new THREE.Mesh(geometry, material);
        solid.name = mesh.name || `mesh-${{index + 1}}`;
        root.add(solid);
        solidObjects.push(solid);

        const edges = new THREE.LineSegments(
          new THREE.EdgesGeometry(geometry, 1),
          new THREE.LineBasicMaterial({{ color: 0x0f172a, transparent: true, opacity: 0.7 }})
        );
        root.add(edges);
        edgeObjects.push(edges);
      }});

      payload.wires.forEach((wire) => {{
        const geometry = new THREE.BufferGeometry().setFromPoints(
          wire.map((p) => new THREE.Vector3(p[0], p[1], p[2]))
        );
        const line = new THREE.Line(
          geometry,
          new THREE.LineBasicMaterial({{ color: 0x111827, linewidth: 2 }})
        );
        root.add(line);
        edgeObjects.push(line);
      }});

      const box = new THREE.Box3().setFromObject(root);
      const center = new THREE.Vector3();
      const size = new THREE.Vector3();
      box.getCenter(center);
      box.getSize(size);
      const radius = Math.max(size.x, size.y, size.z, 1);

      function resetCamera() {{
        controls.target.copy(center);
        camera.near = Math.max(radius / 1000, 0.001);
        camera.far = Math.max(radius * 1000, 1000);
        camera.position.set(center.x + radius * 1.35, center.y - radius * 1.8, center.z + radius * 1.25);
        camera.updateProjectionMatrix();
        controls.update();
      }}

      function resize() {{
        const bounds = viewer.getBoundingClientRect();
        const width = Math.max(Math.floor(bounds.width), 1);
        const h = Math.max(Math.floor(bounds.height), 1);
        renderer.setSize(width, h);
        camera.aspect = width / h;
        camera.updateProjectionMatrix();
      }}

      function isEmbeddedFullscreen() {{
        return viewer.classList.contains("embedded-fullscreen");
      }}

      function setEmbeddedFullscreen(enabled) {{
        viewer.classList.toggle("embedded-fullscreen", enabled);
        fullscreenButton.classList.toggle("active", enabled);
        fullscreenButton.textContent = enabled ? "×" : "⛶";
        setTimeout(resize, 0);
        setTimeout(resize, 120);
      }}

      async function toggleFullscreen() {{
        if (document.fullscreenElement) {{
          await document.exitFullscreen();
          return;
        }}

        if (isEmbeddedFullscreen()) {{
          setEmbeddedFullscreen(false);
          return;
        }}

        if (viewer.requestFullscreen) {{
          try {{
            await viewer.requestFullscreen();
            return;
          }} catch (_error) {{
            setEmbeddedFullscreen(true);
            return;
          }}
        }}
        setEmbeddedFullscreen(true);
      }}

      let solidsVisible = true;
      modeButton.classList.add("active");
      modeButton.addEventListener("click", () => {{
        solidsVisible = !solidsVisible;
        solidObjects.forEach((object) => object.visible = solidsVisible);
        modeButton.classList.toggle("active", solidsVisible);
      }});
      resetButton.addEventListener("click", resetCamera);
      fullscreenButton.addEventListener("click", toggleFullscreen);
      document.addEventListener("fullscreenchange", () => {{
        const active = document.fullscreenElement === viewer;
        fullscreenButton.classList.toggle("active", active);
        fullscreenButton.textContent = active ? "×" : "⛶";
        setTimeout(resize, 0);
      }});
      window.addEventListener("keydown", (event) => {{
        if (event.key === "Escape" && isEmbeddedFullscreen()) {{
          setEmbeddedFullscreen(false);
        }}
      }});
      window.addEventListener("resize", resize);

      resetCamera();
      status.textContent = `${{payload.meshes.length}} mesh / ${{payload.wires.length}} wire`;

      function animate() {{
        requestAnimationFrame(animate);
        controls.update();
        renderer.render(scene, camera);
      }}
      animate();
    }} catch (error) {{
      status.textContent = `Three.js viewer failed: ${{error && error.message ? error.message : error}}`;
    }}
  </script>
</body>
</html>"""


def _valid_face(a: int, b: int, c: int, vertex_count: int) -> bool:
    return (
        isinstance(a, int)
        and isinstance(b, int)
        and isinstance(c, int)
        and 0 <= a < vertex_count
        and 0 <= b < vertex_count
        and 0 <= c < vertex_count
    )
