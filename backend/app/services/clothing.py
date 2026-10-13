"""Assinatura de cor da roupa (tronco e pernas): descritor explicável, complementar ao embedding ReID.

- O recorte do corpo é dividido em tronco (20–55 % da altura) e pernas (55–95 %); a cabeça e os pés ficam de fora.
- Normalização "gray-world" pelo frame inteiro, para atenuar diferenças de balanço de brancos entre câmaras.
- Histograma HSV (matiz × saturação, mais um canal de "acromáticos" por luminosidade) por região e as cores
  dominantes com nome em português, para o perito poder descrever a evidência ("tronco vermelho, pernas azul-escuro").
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

REGIONS = {"tronco": (0.20, 0.55), "pernas": (0.55, 0.95)}
H_BINS, S_BINS, V_BINS = 18, 3, 4  # cromáticos: 18 matizes (20°) × 3 saturações; acromáticos: 4 luminosidades
SAT_MIN = 60  # abaixo disto (0–255) a cor é considerada acromática (preto/cinzento/branco)

_HUE_NAMES = [  # matiz OpenCV (0–180) → nome
    (8, "vermelho"), (20, "laranja"), (33, "amarelo"), (78, "verde"), (95, "ciano"),
    (130, "azul"), (150, "roxo"), (170, "rosa"), (181, "vermelho"),
]


def frame_gain(frame: np.ndarray) -> np.ndarray:
    """Ganho "gray-world" por canal, calculado no frame inteiro: o tom (balanço de brancos) é da câmara, não da
    roupa. Limitado a ±25 % para não distorcer cenas dominadas por uma cor."""
    small = cv2.resize(frame, (160, 90), interpolation=cv2.INTER_AREA) if frame.size else frame
    means = small.reshape(-1, 3).astype(np.float32).mean(axis=0)
    return np.clip(means.mean() / np.maximum(means, 1.0), 0.8, 1.25).astype(np.float32)


def _apply_gain(img: np.ndarray, gain: np.ndarray | None) -> np.ndarray:
    if gain is None:
        return img
    return np.clip(img.astype(np.float32) * gain, 0, 255).astype(np.uint8)


def _region(crop: np.ndarray, frac: tuple[float, float]) -> np.ndarray:
    h, w = crop.shape[:2]
    y0, y1 = int(h * frac[0]), max(int(h * frac[0]) + 1, int(h * frac[1]))
    x0, x1 = int(w * 0.15), max(int(w * 0.15) + 1, int(w * 0.85))  # margens laterais: fundo
    return crop[y0:y1, x0:x1]


def _hist(region: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV).reshape(-1, 3).astype(np.int32)
    if len(hsv) == 0:
        return np.zeros(H_BINS * S_BINS + V_BINS, dtype=np.float32)
    h, s, v = hsv[:, 0], hsv[:, 1], hsv[:, 2]
    chroma = (s >= SAT_MIN) & (v >= 40)
    hb = np.minimum(h * H_BINS // 180, H_BINS - 1)
    sb = np.minimum((s - SAT_MIN) * S_BINS // (256 - SAT_MIN), S_BINS - 1)
    vb = np.minimum(v * V_BINS // 256, V_BINS - 1)
    idx = np.where(chroma, hb * S_BINS + np.maximum(sb, 0), H_BINS * S_BINS + vb)
    hist = np.bincount(idx, minlength=H_BINS * S_BINS + V_BINS).astype(np.float32)
    hist = _smooth(hist)
    return hist / max(hist.sum(), 1.0)


def _smooth(hist: np.ndarray) -> np.ndarray:
    """Suaviza entre bins vizinhos (matiz circular, saturação e luminosidade): pequenas variações de iluminação
    entre câmaras não devem mudar a cor de bin."""
    k = np.array([0.25, 0.5, 0.25], dtype=np.float32)
    chroma = hist[: H_BINS * S_BINS].reshape(H_BINS, S_BINS)
    chroma = sum(w * np.roll(chroma, d, axis=0) for w, d in zip(k, (-1, 0, 1), strict=True))  # matiz (circular)
    padded = np.pad(chroma, ((0, 0), (1, 1)), mode="edge")
    chroma = sum(w * padded[:, i:i + S_BINS] for i, w in enumerate(k))
    achro = np.pad(hist[H_BINS * S_BINS:], 1, mode="edge")
    achro = sum(w * achro[i:i + V_BINS] for i, w in enumerate(k))
    return np.concatenate([chroma.reshape(-1), achro]).astype(np.float32)


def _name(region: np.ndarray) -> str:
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV).reshape(-1, 3).astype(np.int32)
    if len(hsv) == 0:
        return "?"
    h, s, v = hsv[:, 0], hsv[:, 1], hsv[:, 2]
    chroma = (s >= SAT_MIN) & (v >= 40)
    if chroma.mean() < 0.35:
        mv = float(np.median(v))
        return "preto" if mv < 60 else "cinzento-escuro" if mv < 110 else "cinzento" if mv < 180 else "branco"
    hue = int(np.median(h[chroma]))
    name = next(n for lim, n in _HUE_NAMES if hue < lim)
    if float(np.median(v[chroma])) < 110 and name not in ("amarelo",):
        name += "-escuro"
    return name


@dataclass(frozen=True)
class ColorSignature:
    hists: dict[str, np.ndarray]
    names: dict[str, str]

    def to_json(self) -> dict:
        return {"names": self.names, "hists": {k: [round(float(x), 4) for x in v] for k, v in self.hists.items()}}

    @staticmethod
    def from_json(data: dict) -> ColorSignature:
        return ColorSignature({k: np.asarray(v, dtype=np.float32) for k, v in data["hists"].items()},
                              dict(data["names"]))


def color_signature(crop: np.ndarray, gain: np.ndarray | None = None) -> ColorSignature:
    """gain: ver frame_gain() (do frame de onde vem o recorte)."""
    norm = _apply_gain(crop, gain) if crop.size else crop
    hists, names = {}, {}
    for region, frac in REGIONS.items():
        part = _region(norm, frac) if crop.size else crop
        hists[region] = _hist(part)
        names[region] = _name(_region(crop, frac)) if crop.size else "?"  # nome pela cor observada
    return ColorSignature(hists, names)


def color_distance(a: ColorSignature, b: ColorSignature) -> float:
    """Distância de Bhattacharyya média das regiões (0 = iguais, 1 = sem sobreposição)."""
    ds = []
    for region in REGIONS:
        bc = float(np.sum(np.sqrt(a.hists[region] * b.hists[region])))
        ds.append(float(np.sqrt(max(0.0, 1.0 - bc))))
    return float(np.mean(ds))


def mean_signature(sigs: list[ColorSignature]) -> ColorSignature:
    """Assinatura média (amostras de vários frames/câmaras); os nomes são os mais frequentes."""
    hists = {r: np.mean([s.hists[r] for s in sigs], axis=0) for r in REGIONS}
    names = {}
    for r in REGIONS:
        values = [s.names[r] for s in sigs]
        names[r] = max(set(values), key=values.count)
    return ColorSignature(hists, names)
