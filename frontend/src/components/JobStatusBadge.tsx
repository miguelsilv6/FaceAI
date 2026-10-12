import { Badge } from "@mantine/core";

import type { JobStatus } from "../types";

const LABELS: Record<JobStatus, [string, string]> = {
  queued: ["Em fila", "gray"],
  running: ["A decorrer", "blue"],
  completed: ["Concluída", "green"],
  failed: ["Falhou", "red"],
  cancelled: ["Cancelada", "orange"],
};

export default function JobStatusBadge({ status }: { status: JobStatus }) {
  const [label, color] = LABELS[status];
  return <Badge color={color} variant="light">{label}</Badge>;
}
