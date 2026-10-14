"""Melhoria de imagem só para visualização (revisão humana), com operações clássicas e determinísticas.

Nada aqui altera as evidências: o original, os recortes, o relatório e o ZIP dos frames validados usam sempre
as imagens sem tratamento. As operações não "inventam" detalhe (sem IA generativa / super-resolução por rede
neuronal): ampliam (Lanczos), equalizam o contraste localmente (CLAHE), corrigem a gama, reduzem ruído e
aumentam a nitidez (unsharp mask). Ordem fixa: ruído → ampliação → contraste → gama → nitidez.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

MAX_SIDE = 4096
OPS = ("denoise", "clahe", "sharpen")


@dataclass(frozen=True)
class Enhancement:
    denoise: bool = False
    clahe: bool = False
    sharpen: bool = False
    upscale: int = 1  # 1, 2 ou 4
    gamma: float = 1.0  # < 1 clareia as sombras, > 1 escurece

    @classmethod
    def parse(cls, ops: str, upscale: int, gamma: float) -> Enhancement:
        names = {o.strip() for o in (ops or "").split(",") if o.strip()}
        unknown = names - set(OPS)
        if unknown:
            raise ValueError(f"Operação desconhecida: {', '.join(sorted(unknown))}.")
        if upscale not in (1, 2, 4):
            raise ValueError("A ampliação tem de ser 1, 2 ou 4.")
        if not 0.3 <= gamma <= 3.0:
            raise ValueError("A gama tem de estar entre 0,3 e 3.")
        return cls(denoise="denoise" in names, clahe="clahe" in names, sharpen="sharpen" in names,
                   upscale=upscale, gamma=round(gamma, 2))

    @property
    def is_identity(self) -> bool:
        return not (self.denoise or self.clahe or self.sharpen) and self.upscale == 1 and self.gamma == 1.0

    def describe(self) -> str:
        parts = [n for n in ("denoise", "clahe", "sharpen") if getattr(self, n)]
        if self.upscale > 1:
            parts.append(f"x{self.upscale}")
        if self.gamma != 1.0:
            parts.append(f"gama {self.gamma}")
        return " + ".join(parts) or "nenhuma"

    def as_dict(self) -> dict:
        return {"denoise": self.denoise, "clahe": self.clahe, "sharpen": self.sharpen, "upscale": self.upscale,
                "gamma": self.gamma}


def apply(img: np.ndarray, e: Enhancement) -> np.ndarray:
    out = img
    if e.denoise:
        out = cv2.fastNlMeansDenoisingColored(out, None, h=6, hColor=6, templateWindowSize=7, searchWindowSize=21)
    if e.upscale > 1:
        h, w = out.shape[:2]
        f = min(e.upscale, MAX_SIDE / max(h, w))
        if f > 1:
            out = cv2.resize(out, (int(round(w * f)), int(round(h * f))), interpolation=cv2.INTER_LANCZOS4)
    if e.clahe:
        lab = cv2.cvtColor(out, cv2.COLOR_BGR2LAB)
        lab[..., 0] = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(lab[..., 0])
        out = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    if e.gamma != 1.0:
        lut = np.array([((i / 255.0) ** e.gamma) * 255 for i in range(256)]).clip(0, 255).astype(np.uint8)
        out = cv2.LUT(out, lut)
    if e.sharpen:
        blur = cv2.GaussianBlur(out, (0, 0), sigmaX=1.2)
        out = cv2.addWeighted(out, 1.6, blur, -0.6, 0)
    return out


def watermark(img: np.ndarray, e: Enhancement) -> np.ndarray:
    """Faixa no topo a indicar que a imagem foi tratada (para downloads)."""
    text = f"MELHORADA ({e.describe()}) - NAO E O ORIGINAL"
    out = img.copy()
    scale = max(0.4, min(1.2, out.shape[1] / 1400))
    (tw, th), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
    pad = 6
    cv2.rectangle(out, (0, 0), (min(out.shape[1], tw + 2 * pad), th + base + 2 * pad), (0, 0, 0), -1)
    cv2.putText(out, text, (pad, th + pad), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 215, 255), 1, cv2.LINE_AA)
    return out
