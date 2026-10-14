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
    # Motor facial: "onnx" (SCRFD + ArcFace via ONNX Runtime, por omissão) ou "deepface" (TensorFlow, opcional).
    engine: str = field(default_factory=lambda: _env("ENGINE", "onnx"))
    # Pasta dos pesos ONNX (descarregados na 1.ª utilização e verificados por SHA-256).
    models_dir: Path = field(
        default_factory=lambda: Path(_env("MODELS_DIR", str(Path.home() / ".faceai" / "models"))).resolve()
    )
    # Tamanho de cada bloco do upload em blocos (abaixo do limite de 100 MB por pedido do Cloudflare).
    upload_chunk_mb: int = field(default_factory=lambda: int(_env("UPLOAD_CHUNK_MB", "8")))
    cors_origins: list[str] = field(
        default_factory=lambda: [o for o in _env("CORS_ORIGINS", "http://localhost:5173").split(",") if o]
    )
    # --- Autenticação ---
    # Chave para cifrar os segredos TOTP. Vazio = ficheiro data_dir/secret.key (criado com permissões 0600).
    secret_key: str = field(default_factory=lambda: _env("SECRET_KEY", ""))
    # Cookie "Secure": "auto" (só quando o pedido chega por HTTPS, incluindo via Cloudflare), "true" ou "false".
    cookie_secure: str = field(default_factory=lambda: _env("COOKIE_SECURE", "auto").lower())
    # Origens extra aceites em pedidos que alteram dados (além do próprio host), ex.: https://faceai.exemplo.pt
    allowed_origins: list[str] = field(
        default_factory=lambda: [o.rstrip("/") for o in _env("ALLOWED_ORIGINS", "").split(",") if o]
    )
    # "none" ou "cloudflare": confiar em CF-Connecting-IP / X-Forwarded-Proto (só atrás do Cloudflare Tunnel).
    trust_proxy: str = field(default_factory=lambda: _env("TRUST_PROXY", "none").lower())
    session_idle_minutes: int = field(default_factory=lambda: int(_env("SESSION_IDLE_MINUTES", "30")))
    session_max_hours: int = field(default_factory=lambda: int(_env("SESSION_MAX_HOURS", "12")))
    # Documentação interativa da API (/docs): desligada por omissão em produção.
    api_docs: bool = field(default_factory=lambda: _env("API_DOCS", "false").lower() == "true")

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
