"""Importação em bloco a partir de uma pasta do servidor: cada pasta é um local."""

from __future__ import annotations

import dataclasses
import shutil
import time

from .fakes import BLUE, RED, make_video


def _enable(monkeypatch, root):
    import app.api.imports as imports_api
    from app.services import bulk_import

    s = dataclasses.replace(bulk_import.settings, import_dir=root.resolve())
    monkeypatch.setattr(bulk_import, "settings", s)
    monkeypatch.setattr(imports_api, "settings", s)


def _tree(tmp_path):
    root = tmp_path / "import"
    (root / "Café Central" / "cam2").mkdir(parents=True)
    (root / "Banco").mkdir()
    a = make_video(root / "Café Central" / "entrada.mp4", [(1, 2, RED)], duration_s=3, fps=10)
    make_video(root / "Café Central" / "cam2" / "balcao.mp4", [(1, 2, BLUE)], duration_s=4, fps=10)
    make_video(root / "Banco" / "atm.mp4", [(1, 2, RED)], duration_s=5, fps=10)
    shutil.copy(a, root / "Banco" / "copia_entrada.mp4")  # duplicado dentro da importação
    make_video(root / "solto.mp4", [(1, 2, RED)], duration_s=6, fps=10)
    (root / "notas.txt").write_text("não é vídeo")
    return root


def test_server_folder_import(client, tmp_path, monkeypatch):
    cid = client.post("/api/cases", json={"name": "Bloco"}).json()["id"]
    assert client.get(f"/api/cases/{cid}/import/server").json() == {"enabled": False}
    root = _tree(tmp_path)
    _enable(monkeypatch, root)
    scan = client.get(f"/api/cases/{cid}/import/server").json()
    assert scan["enabled"] and scan["path"] == "" and scan["parent"] is None
    names = [g["location_name"] for g in scan["plan"]]
    assert names == ["Pasta de importação", "Banco", "Café Central"]
    cafe = scan["plan"][2]
    assert {v["path"] for v in cafe["videos"]} == {"Café Central/entrada.mp4", "Café Central/cam2/balcao.mp4"}
    assert client.get(f"/api/cases/{cid}/import/server", params={"path": "../.."}).status_code == 403
    folders = [{"path": g["path"], "location_name": g["location_name"] if g["path"] else "Rua",
                "recursive": g["path"] != scan["path"]} for g in scan["plan"]]
    r = client.post(f"/api/cases/{cid}/import/server", json={"folders": folders})
    assert r.status_code == 200 and r.json()["running"] and r.json()["total"] == 5
    deadline = time.time() + 60
    while (st := client.get(f"/api/cases/{cid}/import/server/status").json())["running"]:
        assert time.time() < deadline
        time.sleep(0.1)
    assert st["imported"] == 4 and len(st["duplicates"]) == 1 and not st["errors"], st
    # pela ordem do plano, a cópia em "Banco" entra primeiro e o original em "Café Central" é o repetido
    assert st["duplicates"][0]["path"] == "Café Central/entrada.mp4"
    assert sorted(st["locations_created"]) == ["Banco", "Café Central", "Rua"]
    videos = client.get(f"/api/cases/{cid}/videos").json()
    locs = {loc["id"]: loc["name"] for loc in client.get(f"/api/cases/{cid}/locations").json()}
    assert sorted((locs[v["location_id"]], v["filename"]) for v in videos) == [
        ("Banco", "atm.mp4"), ("Banco", "copia_entrada.mp4"), ("Café Central", "balcao.mp4"),
        ("Rua", "solto.mp4")]
    assert all(v["sha256"] for v in videos)
    assert (root / "solto.mp4").exists()  # os originais ficam intactos
    actions = [e["action"] for e in client.get(f"/api/cases/{cid}/audit").json()]
    assert "import.server_started" in actions and "import.server_finished" in actions
    # a segunda importação das mesmas pastas não duplica nada e reutiliza os locais
    client.post(f"/api/cases/{cid}/import/server", json={"folders": folders})
    while (st := client.get(f"/api/cases/{cid}/import/server/status").json())["running"]:
        time.sleep(0.1)
    assert st["imported"] == 0 and len(st["duplicates"]) == 5 and st["locations_created"] == []
