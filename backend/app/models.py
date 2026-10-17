"""Modelos de dados (SQLModel/SQLite)."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(UTC)


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class ReviewStatus(StrEnum):
    pending = "pending"
    confirmed = "confirmed"
    rejected = "rejected"


class Case(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    description: str = ""
    analyst: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class CasePerson(SQLModel, table=True):
    """Pessoa de um caso (ex.: "Suspeito A") com as suas fotos de referência (vários ângulos)."""

    id: int | None = Field(default=None, primary_key=True)
    case_id: int = Field(foreign_key="case.id", index=True)
    name: str
    notes: str = ""
    created_by: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class ReferenceImage(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    case_id: int = Field(foreign_key="case.id", index=True)
    filename: str
    stored_path: str
    size_bytes: int
    width: int = 0
    height: int = 0
    face_count: int = 0
    face_crop_path: str | None = None
    valid: bool = False
    message: str = ""
    uploaded_at: datetime = Field(default_factory=utcnow)
    # Pessoa do caso a que a foto pertence (None = foto solta). Fotos da mesma pessoa = vários ângulos.
    case_person_id: int | None = Field(default=None, foreign_key="caseperson.id", index=True)
    # Deteção de onde a foto veio ("Adicionar esta face à pessoa"), se não foi carregada pelo utilizador.
    source_detection_id: int | None = None
    person_label: str = ""  # obsoleto (versão anterior: rótulo de texto); convertido em CasePerson no arranque


class Location(SQLModel, table=True):
    """Local de um caso (ex.: "Estação de Campanhã"); agrupa os vídeos das várias câmaras desse local."""

    id: int | None = Field(default=None, primary_key=True)
    case_id: int = Field(foreign_key="case.id", index=True)
    name: str
    created_by: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class Video(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    case_id: int = Field(foreign_key="case.id", index=True)
    # Local (cada vídeo é uma câmara/ângulo desse local) e nome da câmara ("" → mostra-se o nome do ficheiro)
    location_id: int | None = Field(default=None, foreign_key="location.id", index=True)
    camera_name: str = ""
    filename: str
    sha256: str = Field(default="", index=True)  # integridade e deteção de duplicados no processo
    quick_hash: str = Field(default="", index=True)  # SHA-256(tamanho ‖ 1.º MiB ‖ último MiB) — pré-verificação
    stored_path: str
    size_bytes: int
    duration_s: float = 0.0
    fps: float = 0.0
    frame_count: int = 0
    width: int = 0
    height: int = 0
    codec: str = ""
    playback_path: str | None = None  # cópia H.264 só para visualização no navegador
    # "" (por avaliar) | not_needed | pending | running | ready | failed | unavailable (sem ffmpeg)
    playback_status: str = ""
    playback_progress: float = 0.0
    playback_error: str = ""
    playback_format: str = ""  # formato da cópia: "mp4" (H.264) | "webm" (VP9)
    # Hora real (relógio do DVR): hora_real(t) = dvr_start + t × dvr_rate + clock_offset_s
    dvr_start: str | None = None  # ISO 8601 sem fuso: hora "de parede" do DVR (não é convertida para UTC)
    dvr_rate: float = 1.0
    clock_offset_s: float = 0.0
    time_source: str = ""  # "" | "manual" | "ocr"
    time_note: str = ""
    time_calibration: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    # Leitura automática da hora por OCR na importação: "" | pending | running | done | failed | unavailable
    clock_auto_status: str = ""
    clock_auto_message: str = ""
    uploaded_at: datetime = Field(default_factory=utcnow)


class Job(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    case_id: int = Field(foreign_key="case.id", index=True)
    status: JobStatus = JobStatus.queued
    priority: int = 0  # fila: 1 alta, 0 normal, -1 baixa (maior primeiro; empate → ordem de criação)
    held: bool = False  # retida na fila (não começa até ser libertada)
    params: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    video_ids: list[int] = Field(default_factory=list, sa_column=Column(JSON))
    reference_ids: list[int] = Field(default_factory=list, sa_column=Column(JSON))
    environment: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    progress: float = 0.0
    current_video_id: int | None = None
    frames_processed: int = 0
    faces_detected: int = 0
    matches: int = 0
    frames_sampled: int = 0
    frames_skipped_motion: int = 0
    frames_skipped_noface: int = 0
    tracks: int = 0
    persons: int = 0  # modo descoberta: pessoas distintas (grupos de tracks)
    eta_s: float | None = None  # tempo restante estimado (s) enquanto a análise decorre
    # Análise complementar por vestuário: amostras usadas, calibração e resumo (ver services/body_analysis.py)
    clothing_samples: int = 0
    clothing_summary: dict[str, Any] | None = Field(default_factory=dict, sa_column=Column(JSON))
    message: str = ""
    created_by: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class Detection(SQLModel, table=True):
    """Face detetada num frame cuja distância a uma referência ficou abaixo do limiar de candidato.

    No modo descoberta (sem referência) `reference_id` é nulo e a distância é ao centro do grupo da pessoa.
    """

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="job.id", index=True)
    video_id: int = Field(foreign_key="video.id", index=True)
    reference_id: int | None = Field(default=None, foreign_key="referenceimage.id")
    frame_index: int
    timestamp_s: float
    x: int
    y: int
    w: int
    h: int
    face_confidence: float
    distance: float
    similarity: float
    is_match: bool
    crop_path: str
    frame_path: str
    track_id: int | None = Field(default=None, index=True)
    # Modo procurar com várias pessoas: n.º da pessoa (job.params["persons"]) mais próxima desta face.
    person_id: int | None = None
    kind: str = "face"  # "face" | "body" (vestuário: caixa do corpo inteiro, distância ReID às amostras)
    colors: dict[str, Any] | None = Field(default_factory=dict, sa_column=Column(JSON))  # corpo: cores nomeadas
    quality: float = 0.0
    sharpness: float = 0.0
    size_score: float = 0.0
    frontal: float | None = None
    review_status: ReviewStatus = ReviewStatus.pending
    review_note: str = ""
    reviewed_by: str = ""
    reviewed_at: datetime | None = None


class SavedFrame(SQLModel, table=True):
    """Frame guardado manualmente a partir do leitor da análise; conta como imagem validada nos resultados."""

    id: int | None = Field(default=None, primary_key=True)
    case_id: int = Field(foreign_key="case.id", index=True)
    job_id: int = Field(foreign_key="job.id", index=True)
    video_id: int = Field(foreign_key="video.id", index=True)
    frame_index: int
    timestamp_s: float
    stored_path: str  # PNG do frame original (sem anotações)
    sha256: str
    size_bytes: int = 0
    width: int = 0
    height: int = 0
    note: str = ""
    created_by: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class Alert(SQLModel, table=True):
    """Alerta de uma análise: uma pessoa do processo (com fotos) pode ter aparecido — no modo descoberta, numa das
    pessoas descobertas; no modo procurar, em vários locais. É um indício para revisão, não uma identificação."""

    id: int | None = Field(default=None, primary_key=True)
    case_id: int = Field(foreign_key="case.id", index=True)
    job_id: int = Field(foreign_key="job.id", index=True)
    case_person_id: int = Field(foreign_key="caseperson.id", index=True)
    discovered_person_id: int | None = None  # modo descoberta: n.º da pessoa descoberta na análise
    distance: float = 2.0
    similarity: float = 0.0
    location_ids: list[int] = Field(default_factory=list, sa_column=Column(JSON))
    video_ids: list[int] = Field(default_factory=list, sa_column=Column(JSON))
    n_tracks: int = 0
    first_real: str | None = None
    last_real: str | None = None
    best_detection_id: int | None = None
    highlighted: bool = False  # aparece em ≥ N locais (params.alert_min_locations)
    status: str = "new"  # new | confirmed | dismissed
    note: str = ""
    reviewed_by: str = ""
    reviewed_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)


class Track(SQLModel, table=True):
    """Sequência de deteções da mesma face ao longo do vídeo (tracking)."""

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="job.id", index=True)
    video_id: int = Field(foreign_key="video.id", index=True)
    reference_id: int | None = Field(default=None, foreign_key="referenceimage.id")
    person_id: int | None = Field(default=None, index=True)  # modo descoberta: n.º da pessoa nesta análise
    kind: str = "face"  # "face" | "body"
    # Corpo: "face+vestuário" (amostra: corpo de uma face com correspondência) | "vestuário" (só pela roupa)
    evidence: str = ""
    reid_distance: float | None = None
    color_distance: float | None = None
    face_track_id: int | None = None
    colors: dict[str, Any] | None = Field(default_factory=dict, sa_column=Column(JSON))
    note: str = ""
    start_s: float
    end_s: float
    n_faces: int = 0  # faces associadas ao track (incluindo as não guardadas)
    n_saved: int = 0  # deteções guardadas (candidatas/continuação)
    best_detection_id: int | None = None
    best_distance: float = 2.0
    track_distance: float = 2.0  # média das (até) 3 melhores distâncias — decide o match do track
    best_quality: float = 0.0
    is_match: bool = False
    review_status: ReviewStatus = ReviewStatus.pending
    review_note: str = ""
    reviewed_by: str = ""
    reviewed_at: datetime | None = None


class Upload(SQLModel, table=True):
    """Upload em blocos (retomável) de um vídeo ou imagem de referência."""

    id: str = Field(primary_key=True)
    case_id: int = Field(foreign_key="case.id", index=True)
    kind: str  # "video" | "reference"
    location_id: int | None = None  # vídeo: local de destino (None → "Geral")
    quick_hash: str = ""  # vídeo: impressão rápida enviada pelo browser (deteção de duplicados)
    filename: str
    size_bytes: int
    received_bytes: int = 0
    temp_path: str
    created_by: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    completed_at: datetime | None = None
    result_id: int | None = None  # Video/ReferenceImage criado na conclusão (torna /complete idempotente)


class UserRole(StrEnum):
    admin = "admin"
    analyst = "analyst"


class User(SQLModel, table=True):
    """Utilizador local. Password Argon2id; MFA TOTP obrigatório (segredo cifrado com a chave do servidor)."""

    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    display_name: str = ""
    role: UserRole = UserRole.analyst
    password_hash: str
    must_change_password: bool = True
    password_changed_at: datetime | None = None
    totp_secret_enc: str | None = None
    totp_enabled: bool = False
    totp_last_step: int = 0  # último passo TOTP aceite (anti-replay)
    recovery_codes: list[str] | None = Field(default_factory=list, sa_column=Column(JSON))  # hashes SHA-256
    active: bool = True
    failed_logins: int = 0
    locked_until: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)
    last_login_at: datetime | None = None


class UserSession(SQLModel, table=True):
    """Sessão: o cookie tem um token aleatório; aqui só fica o seu SHA-256."""

    id: str = Field(primary_key=True)  # sha256(token)
    user_id: int = Field(foreign_key="user.id", index=True)
    stage: str = "ok"  # "mfa" | "mfa_setup" | "password" (passos do login por concluir) | "ok"
    csrf_token: str
    created_at: datetime = Field(default_factory=utcnow)
    last_seen_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    ip: str = ""
    user_agent: str = ""


class CaseMember(SQLModel, table=True):
    """Acesso a um caso (need-to-know). "owner": quem o criou ou a quem foi entregue; gere membros e o caso."""

    id: int | None = Field(default=None, primary_key=True)
    case_id: int = Field(foreign_key="case.id", index=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    role: str = "member"  # "owner" | "member"
    added_by: str = ""
    added_at: datetime = Field(default_factory=utcnow)


class LoginAttempt(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    username: str = ""
    ip: str = Field(default="", index=True)
    at: datetime = Field(default_factory=utcnow, index=True)
    success: bool = False


class AuditEvent(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=utcnow, index=True)
    case_id: int | None = Field(default=None, index=True)
    actor: str = ""
    ip: str = ""
    action: str
    details: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
