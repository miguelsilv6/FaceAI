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
    # --- Recursos ---
    # Threads de CPU para o ONNX Runtime, OpenCV e ffmpeg (0 = automático: todos os núcleos).
    cpu_threads: int = field(default_factory=lambda: int(_env("CPU_THREADS", "0")))
    # "nice" da thread das análises (0–19): valores maiores deixam a interface e os uploads mais fluidos.
    job_nice: int = field(default_factory=lambda: int(_env("JOB_NICE", "10")))
    # Importação em bloco a partir de uma pasta do servidor (ex.: disco USB ou partilha montada). Vazio = desligada.
    # Só esta pasta (e subpastas) pode ser lida; os ficheiros são copiados (os originais ficam intactos).
    import_dir: Path | None = field(
        default_factory=lambda: Path(_env("IMPORT_DIR", "")).resolve() if _env("IMPORT_DIR", "") else None
    )
    # Ao desligar: tempo máximo de espera para a análise em curso parar e voltar à fila (s)
    shutdown_grace_s: float = field(default_factory=lambda: float(_env("SHUTDOWN_GRACE_S", "20")))
    # Lê a data/hora impressa (OCR) dos vídeos logo na importação e acerta o relógio, se a leitura for coerente.
    auto_clock_ocr: bool = field(default_factory=lambda: _env("AUTO_CLOCK_OCR", "1").lower() in ("1", "true", "yes"))
    # Depois de convertido (cópia de análise sem perdas verificada), o ficheiro original é apagado do servidor para
    # poupar espaço; fica o SHA-256 para o relacionar com o original guardado no suporte de origem.
    remove_originals: bool = field(
        default_factory=lambda: _env("REMOVE_ORIGINALS", "1").lower() in ("1", "true", "yes"))
    # Cópias de reprodução (só para ver no browser; a análise usa sempre o original): criadas quando o vídeo é
    # aberto, com no máximo esta altura e esta qualidade, e apagadas ao fim de N dias sem uso (0 = nunca).
    playback_max_height: int = field(default_factory=lambda: int(_env("PLAYBACK_MAX_HEIGHT", "720")))
    playback_crf: int = field(default_factory=lambda: min(51, max(0, int(_env("PLAYBACK_CRF", "28")))))
    playback_ttl_days: float = field(default_factory=lambda: float(_env("PLAYBACK_TTL_DAYS", "14")))
    clock_date_order: str = field(default_factory=lambda: _env("CLOCK_DATE_ORDER", "DMY").upper())
    # Alertas de disco (pasta de dados): % de ocupação e espaço livre mínimo.
    disk_warn_percent: float = field(default_factory=lambda: float(_env("DISK_WARN_PERCENT", "80")))
    disk_critical_percent: float = field(default_factory=lambda: float(_env("DISK_CRITICAL_PERCENT", "90")))
    disk_min_free_gb: float = field(default_factory=lambda: float(_env("DISK_MIN_FREE_GB", "2")))
    # MFA obrigatório para todos ("true") ou opcional, ativado por cada utilizador em "A minha conta" ("false").
    require_mfa: bool = field(default_factory=lambda: _env("REQUIRE_MFA", "false").lower() == "true")
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
