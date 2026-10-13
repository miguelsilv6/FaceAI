"""Exporta o modelo de re-identificação de pessoas OSNet-AIN x1.0 (torchreid, MIT) para ONNX.

Pesos: OSNet-AIN x1.0 treinado no MSMT17 com combineall=True (Model Zoo do torchreid, secção "Cross-domain ReID",
MSMT17 -> Market1501 & DukeMTMC-reID). É o modelo do torchreid pensado para generalizar para câmaras/domínios
novos, sem re-treino — o caso de um estabelecimento com câmaras próprias.

Corre uma vez (ver .github/workflows/reid-model.yml); o ONNX resultante é publicado num release do repositório e a
aplicação descarrega-o e verifica o SHA-256 fixado em app/services/body_engine.py.

Uso: python tools/export_reid_onnx.py <deep-person-reid checkout> <pesos.pth> <saída.onnx>
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import onnxruntime as ort
import torch


def load_osnet_ain(repo: Path):
    # Importa só o ficheiro do modelo (o pacote torchreid puxa dependências de treino que não são precisas).
    spec = importlib.util.spec_from_file_location("osnet_ain", repo / "torchreid" / "models" / "osnet_ain.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.osnet_ain_x1_0(num_classes=1000, pretrained=False, loss="softmax")


def main(repo: str, weights: str, out: str) -> None:
    model = load_osnet_ain(Path(repo))
    ckpt = torch.load(weights, map_location="cpu", weights_only=False)  # checkpoint oficial do Model Zoo
    state = ckpt.get("state_dict", ckpt)
    state = {k.removeprefix("module."): v for k, v in state.items()}
    own = model.state_dict()
    usable = {k: v for k, v in state.items() if k in own and own[k].shape == v.shape}
    skipped = sorted(set(state) - set(usable))
    missing = sorted(set(own) - set(usable))
    assert all(k.startswith("classifier") for k in skipped + missing), (skipped, missing)
    model.load_state_dict(usable, strict=False)
    model.eval()  # em eval o OSNet devolve o vetor de características (512), sem a camada de classificação

    dummy = torch.randn(2, 3, 256, 128)
    torch.onnx.export(model, dummy, out, input_names=["input"], output_names=["features"], opset_version=17,
                      dynamic_axes={"input": {0: "batch"}, "features": {0: "batch"}})

    with torch.no_grad():
        ref = model(dummy).numpy()
    got = ort.InferenceSession(out, providers=["CPUExecutionProvider"]).run(None, {"input": dummy.numpy()})[0]
    err = float(np.abs(ref - got).max())
    assert got.shape == (2, 512) and err < 1e-3, (got.shape, err)

    sha = hashlib.sha256(Path(out).read_bytes()).hexdigest()
    meta = {"file": Path(out).name, "sha256": sha, "max_abs_error_vs_torch": err, "skipped_keys": skipped,
            "weights_sha256": hashlib.sha256(Path(weights).read_bytes()).hexdigest(), "torch": torch.__version__}
    Path(out).with_suffix(".json").write_text(json.dumps(meta, indent=2))
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main(*sys.argv[1:4])
