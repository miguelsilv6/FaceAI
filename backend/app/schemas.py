"""Esquemas de entrada da API."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .models import ReviewStatus


class CaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    analyst: str = ""


class JobParams(BaseModel):
    # Modelo/detetor do motor ativo; vazios = valores por omissão do motor.
    model: str | None = None
    detector: str | None = None
    sample_fps: float = Field(2.0, gt=0, le=60, description="Frames analisados por segundo de vídeo")
    threshold: float | None = Field(None, gt=0, lt=2, description="Distância de cosseno máxima para match")
    candidate_margin: float = Field(
        0.08, ge=0, le=0.5, description="Margem acima do limiar para guardar candidatos para revisão"
    )
    min_face_size: int = Field(20, ge=0, description="Lado mínimo da face, em píxeis")
    min_confidence: float = Field(0.5, ge=0, le=1, description="Confiança mínima do detetor")
    min_quality: float = Field(0.0, ge=0, le=1, description="Score de qualidade mínimo (0 = sem filtro)")
    upscale: float = Field(1.0, ge=1, le=4, description="Ampliação da resolução de deteção (faces pequenas)")
    segment_gap_s: float = Field(2.0, ge=0, description="Intervalo máximo entre deteções do mesmo segmento")
    # Pré-filtro de movimento
    motion_filter: bool = True
    motion_threshold: float = Field(0.002, ge=0, le=1, description="Fração mínima de píxeis alterados")
    keyframe_interval_s: float = Field(5.0, ge=0, description="Analisar sempre pelo menos 1 frame a cada N s")
    # Cascata: detetor rápido primeiro; o detetor principal só corre se houver faces
    cascade: bool = True
    fast_detector: str | None = None
    # Tracking
    tracking: bool = True
    track_gap_s: float = Field(2.0, ge=0, description="Tempo máximo sem ver a face antes de fechar o track")


class JobCreate(BaseModel):
    params: JobParams = Field(default_factory=JobParams)
    video_ids: list[int] | None = None
    reference_ids: list[int] | None = None


class ReviewUpdate(BaseModel):
    status: ReviewStatus
    note: str = ""


class UploadCreate(BaseModel):
    kind: str = Field(pattern="^(video|reference)$")
    filename: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(gt=0)
