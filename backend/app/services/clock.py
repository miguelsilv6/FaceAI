"""Hora real do CCTV: leitura da data/hora impressa na imagem (OCR) e conversão tempo-do-vídeo → hora do DVR.

Modelo: ``hora_dvr(t) = início_dvr + t × taxa``; ``hora_real(t) = hora_dvr(t) + desvio``.
- ``início_dvr`` e ``taxa`` vêm da calibração (manual: um ponto, taxa 1; OCR: ajuste robusto a vários frames —
  a taxa corrige vídeos cujo fps nominal não corresponde ao tempo real, ex. DVR que grava a fps variável);
- ``desvio`` é a correção conhecida do relógio do DVR (ex.: o DVR estava 3 min adiantado → −180 s).
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta

DATE_ORDERS = ("DMY", "MDY", "YMD")

_MONTHS = {
    "jan": 1, "fev": 2, "feb": 2, "mar": 3, "abr": 4, "apr": 4, "mai": 5, "may": 5, "jun": 6, "jul": 7,
    "ago": 8, "aug": 8, "set": 9, "sep": 9, "out": 10, "oct": 10, "nov": 11, "dez": 12, "dec": 12,
}

_TIME = re.compile(r"(?<!\d)(\d{1,2})\s*[:.;]\s*(\d{2})\s*[:.;]\s*(\d{2})(?!\d)(?:\s*([AaPp])\.?\s*[Mm]\.?)?")
# O OCR de uma zona recortada perde muitas vezes o espaço entre a data e a hora ("2026-10-1214:03:27"): com ':'
# como separador, a hora de 2 dígitos pode vir colada a outros dígitos.
_TIME_GLUED = re.compile(r"(\d{2})\s*[:;]\s*(\d{2})\s*[:;]\s*(\d{2})(?!\d)(?:\s*([AaPp])\.?\s*[Mm]\.?)?")
_DATE_YMD = re.compile(r"(?<!\d)(\d{4})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{1,2})(?!\d)")
_DATE_XXY = re.compile(r"(?<!\d)(\d{1,2})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{4}|\d{2})(?!\d)")
_DATE_NAMED = re.compile(r"(?<!\d)(\d{1,2})\s*[-/ .]\s*([A-Za-z]{3})[A-Za-z]*\.?\s*[-/ .]\s*(\d{4})(?!\d)")


def normalize_ocr(text: str) -> str:
    """Corrige confusões típicas do OCR em dígitos (O→0, l/I/|→1) quando estão junto de dígitos/separadores."""
    t = text.replace("：", ":").replace("．", ".")
    for _ in range(2):
        t = re.sub(r"(?<=[\d:\-/.])[Oo](?=[\d:\-/.\s]|$)|(?<=^)[Oo](?=\d)|(?<=\s)[Oo](?=\d)", "0", t)
        t = re.sub(r"(?<=[\d:\-/.])[lI|](?=[\d:\-/.\s]|$)|(?<=\s)[lI|](?=\d)", "1", t)
    return t


def parse_datetime(text: str, date_order: str = "DMY") -> datetime | None:
    """Extrai a data e hora de um texto de OCR de CCTV (vários formatos comuns). Devolve None se não houver."""
    t = normalize_ocr(text)
    # A data também pode ter o formato dd.mm.aa: escolhe a 1.ª hora válida, preferindo a separada por ':'.
    candidates = [m for rx in (_TIME, _TIME_GLUED) for m in rx.finditer(t)
                  if int(m.group(1)) < 24 and int(m.group(2)) < 60 and int(m.group(3)) < 60]
    candidates.sort(key=lambda m: ":" not in m.group(0))
    if not candidates:
        return None
    tm = candidates[0]
    hh, mi, ss = int(tm.group(1)), int(tm.group(2)), int(tm.group(3))
    ampm = (tm.group(4) or "").lower()
    if ampm == "p" and hh < 12:
        hh += 12
    elif ampm == "a" and hh == 12:
        hh = 0
    date = _parse_date(t[:tm.start()] + " " + t[tm.end():], date_order)
    if date is None:
        return None
    try:
        return datetime(date[0], date[1], date[2], hh, mi, ss)
    except ValueError:
        return None


def parse_time_only(text: str) -> tuple[int, int, int] | None:
    """Só a hora (HH:MM:SS), para quando a data é ilegível num frame mas legível nos restantes."""
    t = normalize_ocr(text)
    for rx in (_TIME, _TIME_GLUED):
        for m in rx.finditer(t):
            hh, mi, ss = int(m.group(1)), int(m.group(2)), int(m.group(3))
            ampm = (m.group(4) or "").lower()
            if ampm == "p" and hh < 12:
                hh += 12
            elif ampm == "a" and hh == 12:
                hh = 0
            # só separadores ':'/';' — "12.10.26" é uma data, não uma hora
            if hh < 24 and mi < 60 and ss < 60 and any(c in m.group(0) for c in ":;"):
                return hh, mi, ss
    return None


def infer_missing_dates(readings: list[tuple[float, str, datetime | None]]
                        ) -> list[tuple[datetime | None, bool]]:
    """Completa leituras em que só a hora é legível com a data das leituras completas mais próximas no vídeo.

    Escolhe o dia (anterior, igual ou seguinte) que fica mais perto do esperado, para lidar com a meia-noite.
    Devolve (data/hora, inferida?) por leitura.
    """
    complete = [(v, d) for v, _, d in readings if d is not None]
    out: list[tuple[datetime | None, bool]] = []
    for video_s, text, dvr in readings:
        if dvr is not None or not complete:
            out.append((dvr, False))
            continue
        hms = parse_time_only(text)
        if hms is None:
            out.append((None, False))
            continue
        ref_v, ref_d = min(complete, key=lambda c: abs(c[0] - video_s))
        expected = ref_d + timedelta(seconds=video_s - ref_v)
        base = expected.replace(hour=hms[0], minute=hms[1], second=hms[2], microsecond=0)
        best = min((base + timedelta(days=k) for k in (-1, 0, 1)),
                   key=lambda c: abs((c - expected).total_seconds()))
        out.append((best, True))
    return out


def _parse_date(t: str, order: str) -> tuple[int, int, int] | None:
    if m := _DATE_YMD.search(t):
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    if m := _DATE_NAMED.search(t):
        month = _MONTHS.get(m.group(2).lower()[:3])
        if month:
            return int(m.group(3)), month, int(m.group(1))
    if m := _DATE_XXY.search(t):
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        if order == "MDY" and a <= 12:
            month, day = a, b
        elif order == "DMY" or a > 12:
            day, month = a, b
        else:
            month, day = a, b
        if month > 12 and day <= 12:  # a ordem configurada não faz sentido para esta data
            day, month = month, day
        return y, month, day
    return None


@dataclass(frozen=True)
class ClockSample:
    video_s: float
    text: str
    dvr: datetime | None


@dataclass(frozen=True)
class ClockFit:
    start: datetime  # hora do DVR em t = 0 do vídeo
    rate: float      # segundos de relógio por segundo de vídeo
    inliers: list[int]
    max_residual_s: float

    def at(self, video_s: float) -> datetime:
        return self.start + timedelta(seconds=video_s * self.rate)


INLIER_TOLERANCE_S = 2.0
RATE_LIMITS = (0.9, 1.1)
MIN_SPAN_FOR_RATE_S = 60.0


def fit_clock(samples: list[ClockSample]) -> ClockFit:
    """Ajuste robusto tempo-do-vídeo → relógio do DVR (tipo RANSAC).

    1. Hipóteses: taxa 1 a passar por cada leitura, e a reta definida por cada par de leituras (taxa entre
       0,9 e 1,1). Escolhe-se a que explica mais leituras a menos de 2 s (leituras de OCR erradas ficam de fora).
    2. Com ≥ 3 inliers a cobrir ≥ 60 s de vídeo, refina-se a taxa por mínimos quadrados; caso contrário taxa 1.
    O relógio impresso mostra o segundo truncado, por isso soma-se 0,5 s; a precisão é de cerca de ±0,5–1 s.
    """
    pts = [(i, s.video_s, s.dvr) for i, s in enumerate(samples) if s.dvr is not None]
    if len(pts) < 2:
        raise ValueError("São precisas pelo menos 2 leituras válidas da data/hora.")
    ref = min(p[2] for p in pts)
    xs = {i: x for i, x, _ in pts}
    ys = {i: (d - ref).total_seconds() for i, _, d in pts}
    ids = list(xs)

    hypotheses = [(1.0, ys[i] - xs[i]) for i in ids]
    for a_i, i in enumerate(ids):
        for j in ids[a_i + 1:]:
            if abs(xs[j] - xs[i]) >= MIN_SPAN_FOR_RATE_S / 2:
                b = (ys[j] - ys[i]) / (xs[j] - xs[i])
                if RATE_LIMITS[0] <= b <= RATE_LIMITS[1]:
                    hypotheses.append((b, ys[i] - b * xs[i]))

    def support(rate: float, intercept: float) -> tuple[int, float]:
        res = [abs(ys[i] - (intercept + rate * xs[i])) for i in ids]
        inl = [r for r in res if r <= INLIER_TOLERANCE_S]
        return len(inl), -sum(inl)

    rate, intercept = max(hypotheses, key=lambda h: (support(*h), h[0] == 1.0))
    inliers = [i for i in ids if abs(ys[i] - (intercept + rate * xs[i])) <= INLIER_TOLERANCE_S]
    if len(inliers) < 2 or len(inliers) < 0.5 * len(pts):
        raise ValueError("As leituras de OCR são inconsistentes entre si; verifique a zona e o formato da data.")

    span = max(xs[i] for i in inliers) - min(xs[i] for i in inliers)
    rate, intercept = 1.0, statistics.median(ys[i] - xs[i] for i in inliers)
    if len(inliers) >= 3 and span >= MIN_SPAN_FOR_RATE_S:
        mx = sum(xs[i] for i in inliers) / len(inliers)
        my = sum(ys[i] for i in inliers) / len(inliers)
        sxx = sum((xs[i] - mx) ** 2 for i in inliers)
        b = sum((xs[i] - mx) * (ys[i] - my) for i in inliers) / sxx
        a = my - b * mx
        err_fit = max(abs(ys[i] - (a + b * xs[i])) for i in inliers)
        err_one = max(abs(ys[i] - (intercept + xs[i])) for i in inliers)
        if RATE_LIMITS[0] <= b <= RATE_LIMITS[1] and err_fit < err_one - 0.5:
            rate, intercept = b, a
    residual = max(abs(ys[i] - (intercept + rate * xs[i])) for i in inliers)
    start = ref + timedelta(seconds=intercept + 0.5)
    return ClockFit(start=start, rate=rate, inliers=inliers, max_residual_s=round(residual, 3))


def real_time(dvr_start: datetime | str | None, rate: float, offset_s: float, video_s: float) -> datetime | None:
    if not dvr_start:
        return None
    if isinstance(dvr_start, str):
        dvr_start = datetime.fromisoformat(dvr_start)
    return dvr_start + timedelta(seconds=video_s * (rate or 1.0) + (offset_s or 0.0))


def format_real(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""
