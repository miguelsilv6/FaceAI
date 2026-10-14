"""Esquemas de entrada da API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .models import ReviewStatus


class CaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    analyst: str = ""


class MemberAdd(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    role: str = Field("member", pattern="^(owner|member)$")


class CaseUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None


class CaseDelete(BaseModel):
    confirm_case_id: int = Field(description="Número do caso, repetido como confirmação do pedido")
    reason: str = Field("", max_length=1000, description="Motivo da eliminação (fica na auditoria)")


class JobDelete(BaseModel):
    confirm_job_id: int = Field(description="Número da análise, repetido como confirmação do pedido")
    reason: str = Field("", max_length=1000, description="Motivo da eliminação (fica na auditoria)")


class JobParams(BaseModel):
    # "match": procurar a pessoa das imagens de referência; "discover": sem referência, agrupar todas as faces
    # em pessoas distintas (o limiar passa a ser o de agrupamento e o tracking é obrigatório).
    mode: str = Field("match", pattern="^(match|discover)$")
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
    # Complemento por vestuário (re-identificação pelo corpo): amostras = corpos das correspondências faciais
    clothing: bool = False
    clothing_window_h: float = Field(12.0, ge=0, description="Janela temporal em torno das amostras (h); 0 = sem")
    reid_threshold: float | None = Field(None, gt=0, lt=1, description="Distância ReID máxima (vazio = omissão)")
    color_threshold: float = Field(0.6, gt=0, le=1, description="Distância de cor máxima (tronco/pernas)")
    person_detector: str | None = None
    min_body_height: int = Field(80, ge=16, description="Altura mínima do corpo (px)")
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


class PersonMerge(BaseModel):
    person_ids: list[int] = Field(min_length=2, description="Pessoas a juntar; ficam com o menor número")


class TrackPerson(BaseModel):
    person_id: int | None = Field(None, ge=1, description="Pessoa de destino; vazio = nova pessoa")


class UploadCreate(BaseModel):
    kind: str = Field(pattern="^(video|reference)$")
    filename: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(gt=0)


class ClockManual(BaseModel):
    """O frame em `video_s` mostra (ou corresponde a) `dvr_time` no relógio do DVR."""

    video_s: float = Field(0.0, ge=0)
    dvr_time: datetime
    clock_offset_s: float = Field(0.0, description="Correção do relógio do DVR para a hora real (s)")
    note: str = ""


class ClockOcr(BaseModel):
    roi: list[float] | None = Field(None, min_length=4, max_length=4,
                                    description="Zona [x, y, largura, altura] normalizada 0–1; vazio = automática")
    samples: int = Field(12, ge=2, le=60)
    date_order: str = Field("DMY", pattern="^(DMY|MDY|YMD)$")
    clock_offset_s: float = 0.0
    note: str = ""


class ClockOcrTest(BaseModel):
    roi: list[float] | None = Field(None, min_length=4, max_length=4)
    t: float = Field(0.0, ge=0)
    date_order: str = Field("DMY", pattern="^(DMY|MDY|YMD)$")


class ClockOffset(BaseModel):
    clock_offset_s: float
    note: str | None = None
