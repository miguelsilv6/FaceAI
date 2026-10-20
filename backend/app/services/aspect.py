"""Proporção de apresentação dos vídeos com píxeis não quadrados (modos "Lite"/meia resolução dos DVR).

Muitos gravadores de CCTV gravam com píxeis que não são quadrados: 960×1080 deve ser mostrado como 1920×1080,
704×288 como 704×576 (4:3)... O leitor do fabricante estica a imagem, mas o ficheiro muitas vezes não indica a
proporção (SAR, sample aspect ratio): os outros leitores, o browser e o OpenCV mostram os píxeis como estão e a
imagem fica quase quadrada e deformada. As faces deformadas também pioram a deteção e o reconhecimento.

- O SAR indicado no próprio vídeo (ffprobe) aplica-se na importação.
- Sem SAR, uma resolução de um modo de DVR conhecido dá uma sugestão, que o responsável confirma (ou não).
- O responsável pode corrigir à mão (16:9, 4:3, tamanho à medida ou original); fica na auditoria.

O ficheiro e o SHA-256 não mudam: a proporção é um dado de apresentação (Video.aspect). Cada análise regista o
tamanho com que leu cada vídeo (job.environment["display"]): os frames são redimensionados para esse tamanho antes
da deteção, e as caixas, os recortes e os frames dessa análise ficam nesse tamanho, mesmo que a proporção do vídeo
mude depois (é preciso repetir a análise para a aplicar).
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from fastapi import HTTPException
from sqlmodel import Session, select

from ..models import Job, Video
from .audit import log_event

INTERPOLATION = cv2.INTER_CUBIC
MAX_SIDE = 8192
log = logging.getLogger(__name__)

# Resoluções gravadas pelos modos de meia resolução dos DVR → (tamanho de apresentação, nome do modo)
KNOWN_MODES: dict[tuple[int, int], tuple[tuple[int, int], str]] = {
    (960, 1080): ((1920, 1080), "1080P Lite / 1080N (meia largura)"),
    (1280, 1440): ((2560, 1440), "4MP Lite (meia largura)"),
    (640, 720): ((1280, 720), "720P Lite / 720N (meia largura)"),
    (960, 576): ((1024, 576), "960H / WD1 PAL (16:9)"),
    (960, 480): ((854, 480), "960H / WD1 NTSC (16:9)"),
    (704, 576): ((768, 576), "D1 PAL (4:3)"),
    (720, 576): ((768, 576), "D1 PAL (4:3)"),
    (704, 480): ((704, 528), "D1 NTSC (4:3)"),
    (720, 480): ((720, 540), "D1 NTSC (4:3)"),
    (704, 288): ((768, 576), "2CIF / Half D1 PAL (meia altura)"),
    (720, 288): ((768, 576), "2CIF / Half D1 PAL (meia altura)"),
    (704, 240): ((704, 528), "2CIF / Half D1 NTSC (meia altura)"),
    (720, 240): ((720, 540), "2CIF / Half D1 NTSC (meia altura)"),
    (352, 288): ((384, 288), "CIF PAL (4:3)"),
    (352, 240): ((352, 264), "CIF NTSC (4:3)"),
}


def _even(n: float) -> int:
    return max(2, int(round(n / 2)) * 2)


def size_for_ratio(width: int, height: int, ratio: float) -> tuple[int, int]:
    """Tamanho de apresentação com a proporção `ratio` (largura/altura), sem reduzir nenhum dos lados."""
    if ratio <= 0:
        raise ValueError("proporção inválida")
    if height * ratio >= width:
        return _even(height * ratio), _even(height)
    return _even(width), _even(width / ratio)


def size_for_sar(width: int, height: int, num: int, den: int) -> tuple[int, int]:
    return size_for_ratio(width, height, width * num / (height * den))


def _ratio(text: str | None) -> tuple[int, int] | None:
    try:
        a, b = (int(x) for x in str(text).split(":"))
    except (TypeError, ValueError):
        return None
    return (a, b) if a > 0 and b > 0 else None


def probe_sar(path: str | Path) -> tuple[int, int] | None:
    """SAR declarado no 1.º stream de vídeo (ffprobe), ou None (sem ffprobe, sem SAR ou SAR 1:1)."""
    if shutil.which("ffprobe") is None:
        return None
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=sample_aspect_ratio",
           "-of", "json", str(path)]
    try:
        out = json.loads(subprocess.run(cmd, check=True, capture_output=True, timeout=60).stdout or b"{}")
    except Exception:  # noqa: BLE001 - ffprobe falhou: fica sem SAR
        return None
    sar = _ratio(((out.get("streams") or [{}])[0]).get("sample_aspect_ratio"))
    return None if sar is None or sar[0] == sar[1] else sar


def display_size(video: Video) -> tuple[int, int] | None:
    """Tamanho de apresentação atual do vídeo, ou None se for o gravado (píxeis quadrados)."""
    d = (video.aspect or {}).get("display")
    if not d or not video.width or not video.height or (int(d[0]), int(d[1])) == (video.width, video.height):
        return None
    return int(d[0]), int(d[1])


def job_display(job: Job, video_id: int) -> tuple[int, int] | None:
    """Tamanho com que a análise leu o vídeo (None: os píxeis gravados, como nas análises anteriores)."""
    d = ((job.environment or {}).get("display") or {}).get(str(video_id))
    return (int(d[0]), int(d[1])) if d else None


def job_displays(videos: list[Video]) -> dict[str, list[int]]:
    """Para job.environment["display"]: só os vídeos com proporção corrigida."""
    return {str(v.id): list(size) for v in videos if (size := display_size(v))}


def fit(image: np.ndarray, size: tuple[int, int] | None) -> np.ndarray:
    """Frame redimensionado para o tamanho de apresentação (sem alteração se já tiver esse tamanho)."""
    if size is None or (image.shape[1], image.shape[0]) == size:
        return image
    return cv2.resize(image, size, interpolation=INTERPOLATION)


def describe(video: Video) -> str:
    size = display_size(video)
    if size is None:
        return ""
    a = video.aspect or {}
    return f"{video.width}×{video.height} gravado, apresentado como {size[0]}×{size[1]} ({a.get('source', '')})"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def detect(s: Session, video: Video) -> None:
    """Na importação (ou depois da extração): aplica o SAR do ficheiro ou guarda uma sugestão."""
    a = dict(video.aspect or {})
    if a.get("source") == "manual" or not video.width or not video.height:
        return
    a["checked_at"] = _now()
    sar = probe_sar(video.source_path)
    if sar is not None:
        size = size_for_sar(video.width, video.height, *sar)
        a = {**a, "display": list(size), "source": f"indicada no ficheiro (SAR {sar[0]}:{sar[1]})",
             "sar": f"{sar[0]}:{sar[1]}", "set_at": _now(), "set_by": "sistema"}
        a.pop("suggested", None)
        video.aspect = a
        s.add(video)
        s.commit()
        log_event(s, "video.aspect_set", case_id=video.case_id, actor="sistema",
                  details={"video_id": video.id, "filename": video.filename, "recorded": [video.width, video.height],
                           "display": list(size), "source": a["source"],
                           "note": "o ficheiro não foi alterado; só a apresentação e a análise usam este tamanho"})
        return
    known = KNOWN_MODES.get((video.width, video.height))
    if known and not a.get("display"):
        a["suggested"] = {"display": list(known[0]), "mode": known[1]}
    video.aspect = a
    s.add(video)
    s.commit()


def backfill() -> None:
    """Vídeos importados antes desta versão: verifica o SAR e a sugestão uma vez (no arranque, em fundo)."""
    from ..db import get_engine

    try:
        with Session(get_engine()) as s:
            ids = [v.id for v in s.exec(select(Video)).all()
                   if not (v.aspect or {}) and v.width and v.extraction_status in ("", "ready")]
        for vid in ids:
            with Session(get_engine()) as s:
                video = s.get(Video, vid)
                if video is not None and Path(video.source_path).exists():
                    detect(s, video)
    except Exception:  # noqa: BLE001 - nunca impede o arranque
        log.exception("verificação da proporção dos vídeos falhou")


def start_backfill() -> None:
    threading.Thread(target=backfill, name="faceai-aspect-backfill", daemon=True).start()


def set_display(s: Session, video: Video, display: tuple[int, int] | None, *, reason: str, actor: str) -> Video:
    """Correção manual (ou confirmação da sugestão). display=None repõe os píxeis gravados."""
    if not video.width or not video.height:
        raise HTTPException(409, "A resolução do vídeo ainda não é conhecida.")
    if display is not None:
        w, h = display
        if not (16 <= w <= MAX_SIDE and 16 <= h <= MAX_SIDE):
            raise HTTPException(422, f"Tamanho inválido: {w}×{h}.")
        if (w, h) == (video.width, video.height):
            display = None
    before = display_size(video)
    a = dict(video.aspect or {})
    history = list(a.get("history") or [])
    history.append({"from": list(before) if before else None, "to": list(display) if display else None,
                    "reason": reason, "by": actor, "at": _now()})
    a = {"history": history, "source": "manual", "set_by": actor, "set_at": _now(), "reason": reason}
    if display is not None:
        a["display"] = list(display)
    video.aspect = a
    s.add(video)
    s.commit()
    s.refresh(video)
    log_event(s, "video.aspect_set", case_id=video.case_id, actor=actor,
              details={"video_id": video.id, "filename": video.filename, "recorded": [video.width, video.height],
                       "before": list(before) if before else None, "display": list(display) if display else None,
                       "source": "manual", "reason": reason,
                       "note": "o ficheiro não foi alterado; as análises já feitas mantêm o tamanho com que foram"
                               " feitas (repita a análise para aplicar a nova proporção)"})
    return video


def summary(video: Video) -> dict[str, Any] | None:
    """Para o relatório: gravado, apresentado, origem."""
    size = display_size(video)
    if size is None:
        return None
    a = video.aspect or {}
    return {"recorded": f"{video.width}×{video.height}", "display": f"{size[0]}×{size[1]}",
            "source": a.get("source", ""), "by": a.get("set_by", ""), "at": a.get("set_at", ""),
            "reason": a.get("reason", "")}
