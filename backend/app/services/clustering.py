"""Agrupamento de tracks em pessoas distintas (modo descoberta, sem imagem de referência).

Cada track é representado pela média dos embeddings das suas melhores faces. Os tracks são agrupados por
centróides: percorrem-se do mais fiável (mais faces, melhor qualidade) para o menos fiável, cada um junta-se ao
grupo cujo centróide está mais perto (se a distância de cosseno for ≤ limiar) ou abre um grupo novo. No fim,
grupos cujos centróides ficaram a ≤ limiar um do outro são fundidos. Ao contrário do "single linkage", um track
intermédio não encadeia duas pessoas diferentes.
"""

from __future__ import annotations

import numpy as np

from .matcher import l2_normalize


def cluster_embeddings(embeddings: np.ndarray, weights: np.ndarray | list[float], threshold: float) -> np.ndarray:
    """Devolve um rótulo 0..P-1 por embedding; o grupo 0 é o de maior peso total."""
    n = len(embeddings)
    if n == 0:
        return np.zeros(0, dtype=int)
    x = l2_normalize(embeddings)
    w = np.maximum(np.asarray(weights, dtype=np.float64), 1e-6)
    sums: list[np.ndarray] = []  # soma ponderada dos embeddings de cada grupo
    labels = np.full(n, -1, dtype=int)
    for i in np.argsort(-w, kind="stable"):
        if sums:
            centroids = l2_normalize(np.stack(sums))
            d = 1.0 - centroids @ x[i]
            j = int(np.argmin(d))
            if d[j] <= threshold:
                labels[i] = j
                sums[j] = sums[j] + w[i] * x[i]
                continue
        labels[i] = len(sums)
        sums.append(w[i] * x[i])

    # Fusão de grupos cujos centróides convergiram (a ordem de chegada pode ter separado a mesma pessoa).
    merged = True
    while merged and len(sums) > 1:
        merged = False
        centroids = l2_normalize(np.stack(sums))
        d = 1.0 - centroids @ centroids.T
        np.fill_diagonal(d, np.inf)
        a, b = np.unravel_index(int(np.argmin(d)), d.shape)
        if d[a, b] <= threshold:
            a, b = min(a, b), max(a, b)
            sums[a] = sums[a] + sums[b]
            del sums[b]
            labels[labels == b] = a
            labels[labels > b] -= 1
            merged = True

    # Renumera por peso total (a pessoa 1 é a que mais aparece).
    totals = np.array([w[labels == k].sum() for k in range(len(sums))])
    order = np.argsort(-totals, kind="stable")
    remap = np.empty_like(order)
    remap[order] = np.arange(len(order))
    return remap[labels]


def centroids_of(embeddings: np.ndarray, weights: np.ndarray | list[float], labels: np.ndarray) -> np.ndarray:
    x = l2_normalize(embeddings)
    w = np.asarray(weights, dtype=np.float64)[:, None]
    k = int(labels.max()) + 1 if len(labels) else 0
    return l2_normalize(np.stack([(x[labels == i] * w[labels == i]).sum(axis=0) for i in range(k)])) if k else x[:0]
