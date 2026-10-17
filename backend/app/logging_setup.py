"""Configuração dos logs: consola (sempre) e, opcionalmente, ficheiro com rotação e formato JSON.

  FACEAI_LOG_FILE=/data/logs/faceai.log   ficheiro com rotação (FACEAI_LOG_MAX_MB, por omissão 20 MB × 5 cópias)
  FACEAI_LOG_FORMAT=json                  uma linha JSON por evento (para ferramentas de recolha de logs)

A thread fica em cada linha (ex.: faceai-job, faceai-transcode), para separar o que é de cada tarefa de fundo.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

TEXT_FORMAT = "%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
               "level": record.levelname, "logger": record.name, "thread": record.threadName,
               "msg": record.getMessage()}
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False)


def configure() -> None:
    fmt: logging.Formatter = (JsonFormatter() if os.environ.get("FACEAI_LOG_FORMAT", "").lower() == "json"
                              else logging.Formatter(TEXT_FORMAT))
    root = logging.getLogger()
    root.setLevel(os.environ.get("FACEAI_LOG_LEVEL", "INFO").upper())
    for h in list(root.handlers):
        root.removeHandler(h)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)
    path = os.environ.get("FACEAI_LOG_FILE", "")
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        max_mb = float(os.environ.get("FACEAI_LOG_MAX_MB", "20"))
        fh = RotatingFileHandler(path, maxBytes=int(max_mb * 1024 * 1024), backupCount=5, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
