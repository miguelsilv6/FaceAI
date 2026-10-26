"""Proporção de apresentação dos vídeos com píxeis não quadrados (modos "Lite"/meia resolução dos DVR).

Muitos gravadores de CCTV gravam com píxeis que não são quadrados: 960×1080 deve ser mostrado como 1920×1080,
704×288 como 704×576 (4:3)... O leitor do fabricante estica a imagem, mas o ficheiro muitas vezes não indica a
proporção (SAR, sample aspect ratio): os outros leitores, o browser e o OpenCV mostram os píxeis como estão e a
imagem fica quase quadrada e deformada. As faces deformadas também pioram a deteção e o reconhecimento.

- Aplica-se automaticamente na importação: o SAR indicado no próprio vídeo (ffprobe) ou, sem SAR, o tamanho do
  modo de DVR conhecido pela resolução.
- O responsável pode alterar à mão no menu do vídeo (automática, 16:9, 4:3, à medida ou a gravada); fica na
  auditoria, e uma escolha manual nunca é substituída pela automática.

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

# Resoluções gravadas pelos modos de DVR → (tamanho de apresentação, nome do modo, aplicar automaticamente).
# Os modos de meia largura/altura não têm ambiguidade; D1, CIF e 960H também são tamanhos de sub-streams de câmaras
# IP 16:9 com píxeis quadrados (ou outra proporção), por isso ficam só como sugestão no menu do vídeo.
KNOWN_MODES: dict[tuple[int, int], tuple[tuple[int, int], str, bool]] = {
    (960, 1080): ((1920, 1080), "1080P Lite / 1080N (meia largura)", True),
    (1280, 1440): ((2560, 1440), "4MP Lite (meia largura)", True),
    (640, 720): ((1280, 720), "720P Lite / 720N (meia largura)", True),
    (704, 288): ((768, 576), "2CIF / Half D1 PAL (meia altura)", True),
    (720, 288): ((768, 576), "2CIF / Half D1 PAL (meia altura)", True),
    (704, 240): ((704, 528), "2CIF / Half D1 NTSC (meia altura)", True),
    (720, 240): ((720, 540), "2CIF / Half D1 NTSC (meia altura)", True),
    (960, 576): ((1024, 576), "960H / WD1 PAL (16:9)", False),
    (960, 480): ((854, 480), "960H / WD1 NTSC (16:9)", False),
    (704, 576): ((768, 576), "D1 PAL (4:3)", False),
    (720, 576): ((768, 576), "D1 PAL (4:3)", False),
    (704, 480): ((704, 528), "D1 NTSC (4:3)", False),
    (720, 480): ((720, 540), "D1 NTSC (4:3)", False),
    (352, 288): ((384, 288), "CIF PAL (4:3)", False),
    (352, 240): ((352, 264), "CIF NTSC (4:3)", False),
}
RULE = 2  # versão da regra automática: os vídeos verificados com uma regra anterior são verificados de novo


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


def probe_sar(path: str | Path) -> tuple[bool, tuple[int, int] | None]:
    """(o ffprobe correu?, SAR declarado diferente de 1:1). Sem ffprobe, falha ou tempo esgotado: (False, None).

    Um SAR 1:1 explícito conta como "sem SAR": muitos DVR escrevem 1:1 mesmo nos modos de meia largura."""
    if shutil.which("ffprobe") is None:
        return False, None
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=sample_aspect_ratio",
           "-of", "json", str(path)]
    try:
        out = json.loads(subprocess.run(cmd, check=True, capture_output=True, timeout=60).stdout or b"{}")
    except Exception:  # noqa: BLE001 - ffprobe falhou: não se sabe
        return False, None
    sar = _ratio(((out.get("streams") or [{}])[0]).get("sample_aspect_ratio"))
    return True, (None if sar is None or sar[0] == sar[1] else sar)


def display_size(video: Video) -> tuple[int, int] | None:
    """Tamanho de apresentação atual do vídeo, ou None se for o gravado (píxeis quadrados)."""
    d = (video.aspect or {}).get("display")
    if not d or not video.width or not video.height or (int(d[0]), int(d[1])) == (video.width, video.height):
        return None
    return int(d[0]), int(d[1])


def _job_entry(job: Job, video_id: int) -> dict[str, Any] | None:
    d = ((job.environment or {}).get("display") or {}).get(str(video_id))
    if not d:
        return None
    return d if isinstance(d, dict) else {"size": d}  # análises anteriores: só [w, h]


def job_display(job: Job, video_id: int) -> tuple[int, int] | None:
    """Tamanho com que a análise leu o vídeo (None: os píxeis gravados, como nas análises anteriores)."""
    d = _job_entry(job, video_id)
    return (int(d["size"][0]), int(d["size"][1])) if d else None


def job_displays(videos: list[Video]) -> dict[str, dict[str, Any]]:
    """Para job.environment["display"]: os vídeos com proporção corrigida, com a origem dessa proporção (o relatório
    da análise mostra a origem com que foi feita, mesmo que a proporção do vídeo mude depois)."""
    out = {}
    for v in videos:
        if size := display_size(v):
            a = v.aspect or {}
            out[str(v.id)] = {"size": list(size), "source": a.get("source", ""), "reason": a.get("reason", ""),
                              "set_by": a.get("set_by", "")}
    return out


def fit(image: np.ndarray, size: tuple[int, int] | None) -> np.ndarray:
    """Frame redimensionado para o tamanho de apresentação (sem alteração se já tiver esse tamanho)."""
    if size is None or (image.shape[1], image.shape[0]) == size:
        return image
    return cv2.resize(image, size, interpolation=INTERPOLATION)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def detect(s: Session, video: Video) -> None:
    """Na importação (ou depois da extração): aplica automaticamente o SAR do ficheiro ou, sem SAR, o tamanho de um
    modo de DVR sem ambiguidade (os outros ficam como sugestão). Uma escolha manual nunca é substituída."""
    if (video.aspect or {}).get("source") == "manual" or not video.width or not video.height:
        return
    ok, sar = probe_sar(video.source_path)
    if not ok:
        return  # sem ffprobe ou falhou: fica por verificar (tenta de novo no próximo arranque)
    s.refresh(video)  # o ffprobe pode demorar: uma escolha manual feita entretanto prevalece
    a = dict(video.aspect or {})
    if a.get("source") == "manual":
        return
    known = KNOWN_MODES.get((video.width, video.height))
    proposal = auto = None
    if sar is not None:
        proposal = auto = {"display": list(size_for_sar(video.width, video.height, *sar)),
                           "mode": f"indicada no ficheiro (SAR {sar[0]}:{sar[1]})"}
    elif known:
        proposal = {"display": list(known[0]), "mode": f"modo de DVR {known[1]}"}
        auto = proposal if known[2] else None
    before = display_size(video)
    a.update({"checked_at": _now(), "rule": RULE})
    if proposal:
        a["suggested"] = proposal
    else:
        a.pop("suggested", None)
    if auto:
        a.update({"display": auto["display"], "source": f"automática: {auto['mode']}", "set_by": "sistema",
                  "set_at": _now()})
    else:
        for k in ("display", "source", "set_by", "set_at", "reason"):
            a.pop(k, None)
    video.aspect = a
    s.add(video)
    s.commit()
    after = display_size(video)
    if after != before:
        _invalidate_copy(s, video)
        log_event(s, "video.aspect_set", case_id=video.case_id, actor="sistema",
                  details={"video_id": video.id, "filename": video.filename, "recorded": [video.width, video.height],
                           "before": list(before) if before else None, "display": list(after) if after else None,
                           "source": a.get("source", "gravada"),
                           "note": "o ficheiro não foi alterado; só a apresentação e a análise usam este tamanho;"
                                   " o responsável pode alterar no menu do vídeo"})


def _invalidate_copy(s: Session, video: Video) -> None:
    """A cópia de reprodução é feita no tamanho de apresentação: com outra proporção, é criada de novo ao abrir."""
    from .transcode import invalidate_copy

    invalidate_copy(s, video)


def unchecked(video: Video) -> bool:
    """Nunca verificado com a regra atual (ex.: importado por uma versão anterior), sem escolha manual."""
    a = video.aspect or {}
    return a.get("source") != "manual" and a.get("rule") != RULE


def backfill() -> None:
    """Vídeos importados antes desta versão: verifica e aplica a proporção uma vez (no arranque, em fundo)."""
    from ..db import get_engine

    try:
        with Session(get_engine()) as s:
            ids = [v.id for v in s.exec(select(Video)).all()
                   if v.width and v.extraction_status in ("", "ready") and unchecked(v)]
        for vid in ids:
            with Session(get_engine()) as s:
                video = s.get(Video, vid)
                if video is not None and Path(video.source_path).exists():
                    detect(s, video)
    except Exception:  # noqa: BLE001 - nunca impede o arranque
        log.exception("verificação da proporção dos vídeos falhou")


def start_backfill() -> None:
    threading.Thread(target=backfill, name="faceai-aspect-backfill", daemon=True).start()


def set_display(s: Session, video: Video, display: tuple[int, int] | None, *, reason: str, actor: str,
                auto: bool = False) -> Video:
    """Escolha do responsável. display=None repõe os píxeis gravados; auto=True volta à proporção automática."""
    if not video.width or not video.height:
        raise HTTPException(409, "A resolução do vídeo ainda não é conhecida.")
    if display is not None and not auto:
        w, h = display
        if not (16 <= w <= MAX_SIDE and 16 <= h <= MAX_SIDE):
            raise HTTPException(422, f"Tamanho inválido: {w}×{h}.")
        if (w, h) == (video.width, video.height):
            display = None
    before = display_size(video)
    a = dict(video.aspect or {})
    history = list(a.get("history") or [])
    if auto:
        video.aspect = {"history": history, **({"suggested": a["suggested"]} if a.get("suggested") else {})}
        s.add(video)
        s.commit()
        detect(s, video)  # volta a aplicar a regra automática (SAR do ficheiro ou modo de DVR)
        s.refresh(video)
        display = display_size(video)
        a = dict(video.aspect or {})
        history.append({"from": list(before) if before else None, "to": list(display) if display else None,
                        "reason": reason, "by": actor, "at": _now(), "auto": True})
        video.aspect = {**a, "history": history}
    else:
        history.append({"from": list(before) if before else None, "to": list(display) if display else None,
                        "reason": reason, "by": actor, "at": _now()})
        a = {"history": history, "source": "manual", "set_by": actor, "set_at": _now(), "reason": reason,
             "rule": RULE, **({"suggested": a["suggested"]} if a.get("suggested") else {})}
        if display is not None:
            a["display"] = list(display)
        video.aspect = a
    s.add(video)
    s.commit()
    s.refresh(video)
    if display_size(video) != before:
        _invalidate_copy(s, video)
    log_event(s, "video.aspect_set", case_id=video.case_id, actor=actor,
              details={"video_id": video.id, "filename": video.filename, "recorded": [video.width, video.height],
                       "before": list(before) if before else None, "display": list(display) if display else None,
                       "source": "automática (reposta)" if auto else "manual", "reason": reason,
                       "note": "o ficheiro não foi alterado; as análises já feitas mantêm o tamanho com que foram"
                               " feitas (repita a análise para aplicar a nova proporção)"})
    return video
