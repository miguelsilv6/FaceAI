"""Esquemas de entrada da API."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .models import ReviewStatus


class CaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    analyst: str = ""


class JobParams(BaseModel):
    model: str = "ArcFace"
    detector: str = "retinaface"
    sample_fps: float = Field(2.0, gt=0, le=60, description="Frames analisados por segundo de vídeo")
    threshold: float | None = Field(None, gt=0, lt=2, description="Distância de cosseno máxima para match")
    candidate_margin: float = Field(
        0.08, ge=0, le=0.5, description="Margem acima do limiar para guardar candidatos para revisão"
    )
    min_face_size: int = Field(20, ge=0, description="Lado mínimo da face, em píxeis")
    min_confidence: float = Field(0.5, ge=0, le=1, description="Confiança mínima do detetor")
    upscale: float = Field(1.0, ge=1, le=4, description="Fator de ampliação dos frames antes da deteção")
    segment_gap_s: float = Field(2.0, ge=0, description="Intervalo máximo entre deteções do mesmo segmento")


class JobCreate(BaseModel):
    params: JobParams = Field(default_factory=JobParams)
    video_ids: list[int] | None = None
    reference_ids: list[int] | None = None


class ReviewUpdate(BaseModel):
    status: ReviewStatus
    note: str = ""
