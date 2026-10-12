from __future__ import annotations

from typing import Annotated
from urllib.parse import unquote

from fastapi import Depends, Header, HTTPException
from sqlmodel import Session

from ..db import get_session
from ..models import Case, Job

SessionDep = Annotated[Session, Depends(get_session)]


def get_actor(x_analyst: Annotated[str | None, Header()] = None) -> str:
    """Nome do analista, enviado pelo frontend no cabeçalho X-Analyst (URL-encoded)."""
    return unquote(x_analyst).strip()[:100] if x_analyst else ""


ActorDep = Annotated[str, Depends(get_actor)]


def get_case_or_404(session: Session, case_id: int) -> Case:
    case = session.get(Case, case_id)
    if case is None:
        raise HTTPException(404, "Caso não encontrado.")
    return case


def get_job_or_404(session: Session, job_id: int) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Análise não encontrada.")
    return job
