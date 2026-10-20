import { Button, Card, Group, Popover, Progress, Stack, Text } from "@mantine/core";
import { IconChecklist, IconFileText, IconInfoCircle } from "@tabler/icons-react";

import { api, formatEta } from "../api";
import type { Job } from "../types";

export interface ReviewCounts {
  total: number;
  pending: number;
  confirmed: number;
  rejected: number;
}

/** Faixa de estado da análise: quanto foi encontrado, quanto falta rever e o passo seguinte. */
export default function ResultsSummary({ job, discover, running, counts, persons, details, onReviewPending }: {
  job: Job;
  discover: boolean;
  running: boolean;
  counts: ReviewCounts;
  persons: number;
  details: string;
  onReviewPending: () => void;
}) {
  const done = job.status === "completed";
  const info = (
    <Popover width={420} position="bottom-start" withArrow shadow="md">
      <Popover.Target>
        <Button size="compact-sm" variant="subtle" color="gray" leftSection={<IconInfoCircle size={14} />}>
          Detalhes da análise
        </Button>
      </Popover.Target>
      <Popover.Dropdown><Text size="xs">{details}</Text></Popover.Dropdown>
    </Popover>
  );

  if (running) {
    return (
      <Card withBorder padding="sm" bg="var(--mantine-color-blue-light)">
        <Stack gap={6}>
          <Group justify="space-between" wrap="wrap">
            <Text fw={600}>A analisar… {Math.round(job.progress * 100)}% · {formatEta(job.eta_s)}</Text>
            {info}
          </Group>
          <Progress value={job.progress * 100} animated striped size="lg" />
          <Text size="xs" c="dimmed">
            Pode sair desta página: a análise continua no servidor. {counts.total > 0
              ? `Já há ${counts.total} ${discover ? "aparições" : "aparições encontradas"}; pode começar a rever.` : ""}
          </Text>
        </Stack>
      </Card>
    );
  }

  const allReviewed = done && counts.total > 0 && counts.pending === 0;
  return (
    <Card withBorder padding="sm">
      <Group justify="space-between" wrap="wrap" gap="sm">
        <div>
          <Text fw={700} size="lg">
            {discover ? `${persons} ${persons === 1 ? "pessoa" : "pessoas"} · ${counts.total} aparições`
              : counts.total === 0 ? "Nenhuma aparição encontrada"
                : `${counts.total} ${counts.total === 1 ? "aparição" : "aparições"}`}
          </Text>
          {counts.total > 0 && (
            <Text size="sm" c="dimmed">
              {counts.pending} por rever · {counts.confirmed} confirmada{counts.confirmed === 1 ? "" : "s"}
              {" "}· {counts.rejected} rejeitada{counts.rejected === 1 ? "" : "s"}
              {allReviewed ? " · revisão concluída" : ""}
            </Text>
          )}
          {done && counts.total === 0 && !discover && (
            <Text size="sm" c="dimmed">
              Experimente outra foto da pessoa ou o rigor "Minuciosa" numa nova análise.
            </Text>
          )}
        </div>
        <Group gap="xs">
          {info}
          {counts.pending > 0 && (
            <Button leftSection={<IconChecklist size={16} />} onClick={onReviewPending}>
              Rever pendentes ({counts.pending})
            </Button>
          )}
          {done && (
            <Button component="a" href={api.exportUrl(job.id, "pdf")} leftSection={<IconFileText size={16} />}
              variant={allReviewed || !counts.total ? "filled" : "light"}>
              Exportar relatório
            </Button>
          )}
        </Group>
      </Group>
    </Card>
  );
}
