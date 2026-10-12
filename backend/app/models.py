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


class Video(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    case_id: int = Field(foreign_key="case.id", index=True)
    filename: str
    stored_path: str
    size_bytes: int
    duration_s: float = 0.0
    fps: float = 0.0
    frame_count: int = 0
    width: int = 0
    height: int = 0
    codec: str = ""
    playback_path: str | None = None  # cópia H.264 só para visualização no navegador
    uploaded_at: datetime = Field(default_factory=utcnow)


class Job(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    case_id: int = Field(foreign_key="case.id", index=True)
    status: JobStatus = JobStatus.queued
    params: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    video_ids: list[int] = Field(default_factory=list, sa_column=Column(JSON))
    reference_ids: list[int] = Field(default_factory=list, sa_column=Column(JSON))
    environment: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    progress: float = 0.0
    current_video_id: int | None = None
    frames_processed: int = 0
    faces_detected: int = 0
    matches: int = 0
    message: str = ""
    created_by: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class Detection(SQLModel, table=True):
    """Face detetada num frame cuja distância a uma referência ficou abaixo do limiar de candidato."""

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="job.id", index=True)
    video_id: int = Field(foreign_key="video.id", index=True)
    reference_id: int = Field(foreign_key="referenceimage.id")
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
    review_status: ReviewStatus = ReviewStatus.pending
    review_note: str = ""
    reviewed_by: str = ""
    reviewed_at: datetime | None = None


class AuditEvent(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=utcnow, index=True)
    case_id: int | None = Field(default=None, index=True)
    actor: str = ""
    action: str
    details: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
