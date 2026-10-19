import { Badge, Tooltip } from "@mantine/core";
import { IconScan } from "@tabler/icons-react";

import { formatDate } from "../api";
import type { LocationAnalysis } from "../places";

const MODES: Record<string, string> = { match: "procurar pessoa", discover: "descoberta" };

/** Indica se o local já foi analisado (todos / alguns / nenhum dos vídeos numa análise concluída). */
export default function AnalysisBadge({ analysis, size = "xs" }: { analysis: LocationAnalysis; size?: "xs" | "sm" }) {
  const { state, analysed, total, jobs } = analysis;
  if (!total) return null;
  const last = jobs[0];
  const label = state === "done" ? `Analisado · #${last.id} ${formatDate(last.finished_at ?? last.created_at).slice(0, 10)}`
    : state === "partial" ? `Parcialmente analisado (${analysed}/${total} vídeos)` : "Por analisar";
  const tip = jobs.length
    ? jobs.slice(0, 8).map((j) => `#${j.id} · ${formatDate(j.finished_at ?? j.created_at)} · ${MODES[j.params.mode] ?? j.params.mode}`
      + ` · ${j.matches} correspondência(s)`).join("\n") + (jobs.length > 8 ? `\n… mais ${jobs.length - 8}` : "")
    : "Nenhum vídeo deste local foi incluído numa análise concluída.";
  return (
    <Tooltip label={tip} multiline w={340} style={{ whiteSpace: "pre-line" }}>
      <Badge size={size} variant="light" leftSection={<IconScan size={10} />}
        color={state === "done" ? "teal" : state === "partial" ? "orange" : "gray"}
        data-testid="location-analysis">
        {label}
      </Badge>
    </Tooltip>
  );
}
