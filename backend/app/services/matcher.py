"""Comparação de embeddings por distância de cosseno."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def l2_normalize(vectors: np.ndarray) -> np.ndarray:
    vectors = np.asarray(vectors, dtype=np.float32)
    if vectors.ndim == 1:
        vectors = vectors[None, :]
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vectors / norms


@dataclass(frozen=True)
class MatchResult:
    reference_index: int
    distance: float

    @property
    def similarity(self) -> float:
        """Similaridade de cosseno (1 - distância), entre -1 e 1."""
        return 1.0 - self.distance


class ReferenceSet:
    """Conjunto de embeddings de referência de uma pessoa (uma ou mais imagens)."""

    def __init__(self, embeddings: np.ndarray):
        if len(embeddings) == 0:
            raise ValueError("É necessária pelo menos uma imagem de referência válida.")
        self.embeddings = l2_normalize(embeddings)

    def distances(self, query: np.ndarray) -> np.ndarray:
        """Distâncias de cosseno (queries x referências)."""
        return 1.0 - l2_normalize(query) @ self.embeddings.T

    def best_match(self, query: np.ndarray) -> MatchResult:
        d = self.distances(query)[0]
        idx = int(np.argmin(d))
        return MatchResult(reference_index=idx, distance=float(d[idx]))


def is_match(distance: float, threshold: float) -> bool:
    return distance <= threshold


class MultiReferenceSet:
    """Referências de várias pessoas: cada face é comparada com todas e atribuída à pessoa mais próxima."""

    def __init__(self, groups: list[np.ndarray]):
        if not groups or any(len(g) == 0 for g in groups):
            raise ValueError("Cada pessoa precisa de pelo menos uma imagem de referência válida.")
        self.embeddings = l2_normalize(np.concatenate(groups))
        self.owner = np.concatenate([np.full(len(g), i) for i, g in enumerate(groups)])
        self.n_persons = len(groups)

    def per_person(self, query: np.ndarray) -> list[tuple[int, float]]:
        """Para cada pessoa: (índice global da referência mais próxima, distância)."""
        d = 1.0 - (l2_normalize(query) @ self.embeddings.T)[0]
        out = []
        for p in range(self.n_persons):
            idx = np.flatnonzero(self.owner == p)
            best = int(idx[np.argmin(d[idx])])
            out.append((best, float(d[best])))
        return out
