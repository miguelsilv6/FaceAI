"""Configuração da aplicação (variáveis de ambiente com prefixo FACEAI_)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(f"FACEAI_{name}", default)


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", "./data")).resolve())
    frontend_dist: Path = field(
        default_factory=lambda: Path(
            _env("FRONTEND_DIST", str(Path(__file__).resolve().parents[2] / "frontend" / "dist"))
        )
    )
    default_model: str = field(default_factory=lambda: _env("DEFAULT_MODEL", "ArcFace"))
    default_detector: str = field(default_factory=lambda: _env("DEFAULT_DETECTOR", "retinaface"))
    cors_origins: list[str] = field(
        default_factory=lambda: _env("CORS_ORIGINS", "http://localhost:5173").split(",")
    )

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.data_dir / 'faceai.db'}"

    @property
    def audit_log_path(self) -> Path:
        return self.data_dir / "audit.jsonl"

    def case_dir(self, case_id: int) -> Path:
        return self.data_dir / "cases" / f"case_{case_id:05d}"

    def ensure_dirs(self) -> None:
        (self.data_dir / "cases").mkdir(parents=True, exist_ok=True)


settings = Settings()

# Modelos de reconhecimento e detetores suportados pelo DeepFace que fazem sentido em CCTV.
RECOGNITION_MODELS = ["ArcFace", "Facenet512", "Facenet", "VGG-Face", "GhostFaceNet", "SFace"]
DETECTOR_BACKENDS = ["retinaface", "mtcnn", "yunet", "centerface", "yolov8n", "opencv", "ssd"]
