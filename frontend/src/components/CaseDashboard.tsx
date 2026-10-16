import {
  Alert, Anchor, Badge, Box, Button, Card, Group, Loader, MultiSelect, Paper, Progress, SimpleGrid, Stack, Table, Text,
  Title, Tooltip,
} from "@mantine/core";
import { IconCheck, IconClock, IconX } from "@tabler/icons-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, formatBytes, formatDate, formatDuration, formatTime } from "../api";
import { notifyError } from "../notify";
import { personColor } from "../personColors";
import type { CaseDashboard as Dashboard, Job } from "../types";

// Estado da revisão: cores de estado reservadas, sempre acompanhadas de ícone e texto (nunca só a cor).
const STATUS = {
  confirmed: { label: "Confirmadas", color: "var(--mantine-color-green-6)", icon: IconCheck },
  rejected: { label: "Rejeitadas", color: "var(--mantine-color-red-6)", icon: IconX },
  pending: { label: "Por rever", color: "light-dark(var(--mantine-color-gray-5), var(--mantine-color-dark-2))", icon: IconClock },
} as const;
type StatusKey = keyof typeof STATUS;
const ORDER: StatusKey[] = ["confirmed", "rejected", "pending"];
const HOUR_COLOR = "var(--mantine-color-indigo-6)"; // magnitude: uma só cor

function Kpi({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <Paper withBorder p="sm" radius="md">
      <Text size="xs" c="dimmed" tt="uppercase" fw={600}>{label}</Text>
      <Text fz={26} fw={700} lh={1.2}>{value}</Text>
      {sub && <Text size="xs" c="dimmed">{sub}</Text>}
    </Paper>
  );
}

function Legend() {
  return (
    <Group gap="md">
      {ORDER.map((k) => {
        const Icon = STATUS[k].icon;
        return (
          <Group key={k} gap={4}>
            <Box w={10} h={10} style={{ background: STATUS[k].color, borderRadius: 2 }} />
            <Icon size={12} />
            <Text size="xs">{STATUS[k].label}</Text>
          </Group>
        );
      })}
    </Group>
  );
}

/** Barra horizontal empilhada (confirmadas / rejeitadas / por rever), com 2px de intervalo entre segmentos. */
function StackedBar({ counts, max }: { counts: Record<StatusKey, number>; max: number }) {
  const total = ORDER.reduce((s, k) => s + counts[k], 0);
  return (
    <Box style={{ display: "flex", gap: 2, height: 14, width: `${max ? (total / max) * 100 : 0}%`, minWidth: total ? 6 : 0 }}>
      {ORDER.filter((k) => counts[k] > 0).map((k, i, arr) => (
        <Tooltip key={k} label={`${STATUS[k].label}: ${counts[k]}`}>
          <Box style={{
            flex: counts[k], background: STATUS[k].color, minWidth: 3,
            borderRadius: `${i === 0 ? 4 : 0}px ${i === arr.length - 1 ? 4 : 0}px ${i === arr.length - 1 ? 4 : 0}px ${i === 0 ? 4 : 0}px`,
          }} />
        </Tooltip>
      ))}
    </Box>
  );
}

/** Histograma das correspondências por hora real (horas sem correspondências incluídas, até 4 dias seguidos). */
function HourHistogram({ data }: { data: Dashboard["per_hour"] }) {
  const bars = useMemo(() => {
    if (!data.length) return [];
    const toDate = (h: string) => new Date(`${h.replace(" ", "T")}:00:00Z`);
    const first = toDate(data[0].hour).getTime();
    const last = toDate(data[data.length - 1].hour).getTime();
    const span = (last - first) / 3600_000 + 1;
    if (span > 96) return data; // intervalo grande: só as horas com correspondências
    const byHour = new Map(data.map((d) => [d.hour, d.matches]));
    return Array.from({ length: span }, (_, i) => {
      const h = new Date(first + i * 3600_000).toISOString().slice(0, 13).replace("T", " ");
      return { hour: h, matches: byHour.get(h) ?? 0 };
    });
  }, [data]);
  const max = Math.max(1, ...bars.map((b) => b.matches));
  const showEvery = Math.max(1, Math.ceil(bars.length / 12));
  const labelValues = bars.length <= 12; // poucas colunas: valor por cima de cada uma
  const col = { flex: 1, maxWidth: 56, display: "flex", justifyContent: "center" } as const;
  const day0 = bars[0]?.hour.slice(0, 10);
  const day1 = bars[bars.length - 1]?.hour.slice(0, 10);
  return (
    <Box>
      <Box style={{ display: "flex", alignItems: "flex-end", justifyContent: "center", gap: 2, height: 150,
        borderBottom: "1px solid var(--mantine-color-default-border)" }}>
        {bars.map((b) => (
          <Tooltip key={b.hour} label={`${b.hour}:00–${b.hour.slice(11)}:59 · ${b.matches} correspondência(s)`}>
            <Box style={{ ...col, height: "100%", flexDirection: "column", justifyContent: "flex-end", cursor: "default" }}>
              {labelValues && b.matches > 0 && <Text size="xs" ta="center" fw={600}>{b.matches}</Text>}
              <Box style={{ width: "100%", height: `${(b.matches / max) * (labelValues ? 85 : 100)}%`,
                minHeight: b.matches ? 3 : 0, background: HOUR_COLOR, borderRadius: "4px 4px 0 0" }} />
            </Box>
          </Tooltip>
        ))}
      </Box>
      <Box style={{ display: "flex", justifyContent: "center", gap: 2 }}>
        {bars.map((b, i) => (
          <Text key={b.hour} size="10px" c="dimmed" ta="center" style={{ ...col, overflow: "hidden", whiteSpace: "nowrap" }}>
            {i % showEvery === 0 ? `${b.hour.slice(11)}h` : ""}
          </Text>
        ))}
      </Box>
      <Text size="xs" c="dimmed" ta="center" mt={2}>{day0 === day1 ? day0 : `${day0} → ${day1}`}</Text>
    </Box>
  );
}

const ACTIONS: Record<string, string> = {
  "job.created": "análise criada", "job.completed": "análise concluída", "detection.reviewed": "deteção revista",
  "track.reviewed": "track revisto", "video.uploaded": "vídeo importado", "video.clock_set": "hora real acertada",
  "reference.uploaded": "foto importada", "person.photo_search": "pesquisa por foto", "video.deleted": "vídeo eliminado",
  "video.updated": "câmara alterada", "location.created": "local criado", "location.updated": "local renomeado",
  "location.deleted": "local eliminado",
  "job.deleted": "análise eliminada", "report.generated": "relatório gerado", "job.started": "análise iniciada",
  "job.failed": "análise falhou", "job.cancelled": "análise cancelada", "video.playback_copy_requested": "cópia de reprodução pedida",
  "video.playback_copy_ready": "cópia de reprodução pronta", "video.clock_ocr_failed": "OCR da hora sem resultado",
  "case.created": "caso criado", "case.updated": "caso alterado", "person.linked_search": "pesquisa noutras análises",
  "frames.validated_downloaded": "frames validados descarregados",
};

/** Painel do caso: material, análises, progresso da revisão, correspondências por câmara, hora e pessoa. */
export default function CaseDashboard({ caseId, jobs, onOpenTab }: {
  caseId: number; jobs: Job[]; onOpenTab: (tab: string) => void;
}) {
  const navigate = useNavigate();
  const completed = jobs.filter((j) => j.status === "completed");
  const [jobIds, setJobIds] = useState<string[]>([]);
  const [data, setData] = useState<Dashboard | null>(null);
  const jobsKey = jobs.map((j) => `${j.id}:${j.status}`).join(",");

  useEffect(() => {
    api.caseDashboard(caseId, jobIds.map(Number)).then(setData).catch(notifyError);
  }, [caseId, jobIds, jobsKey]);

  if (!data) return <Loader size="sm" />;
  const r = data.review;
  const reviewed = r.confirmed + r.rejected;
  const pct = r.matches ? Math.round((reviewed / r.matches) * 100) : 0;
  const running = (data.jobs.by_status.running ?? 0) + (data.jobs.by_status.queued ?? 0);
  const maxCam = Math.max(1, ...data.per_camera.map((c) => c.matches));
  const maxLoc = Math.max(1, ...data.per_location.map((l) => l.matches));
  const maxPerson = Math.max(1, ...data.per_person.map((p) => p.matches));
  const personOrder = [...data.per_person].map((p) => p.label).sort();
  const m = data.material;

  return (
    <Stack>
      <Group justify="space-between" align="flex-end" wrap="wrap">
        <MultiSelect label="Análises consideradas" w={380} value={jobIds} onChange={setJobIds} clearable
          placeholder="Todas as concluídas"
          data={completed.map((j) => ({ value: String(j.id),
            label: `#${j.id} · ${j.params.mode === "discover" ? "descoberta" : (j.params.persons ?? []).map((p) => p.label).join(", ") || "procurar"}` }))} />
        {data.pending_by_job.length > 0 && (
          <Button variant="light" leftSection={<IconClock size={16} />}
            onClick={() => navigate(`/jobs/${data.pending_by_job[0].job_id}`)}>
            Rever pendentes (análise #{data.pending_by_job[0].job_id}: {data.pending_by_job[0].pending})
          </Button>
        )}
      </Group>

      <SimpleGrid cols={{ base: 2, md: 4 }}>
        <Kpi label="Locais / câmaras" value={`${m.locations} / ${m.videos}`}
          sub={`${formatDuration(m.duration_s)} · ${m.videos - m.videos_synced} sem hora real · ${formatBytes(m.size_bytes)}`} />
        <Kpi label="Análises concluídas" value={String(data.jobs.by_status.completed ?? 0)}
          sub={`${running} em curso · ${data.jobs.by_status.failed ?? 0} falhada(s)`} />
        <Kpi label="Correspondências" value={String(r.matches)}
          sub={`${r.tracks} track(s)${r.clothing ? ` · ${r.clothing} só pelo vestuário` : ""}`} />
        <Kpi label="Revisão" value={`${pct}%`} sub={`${reviewed} de ${r.matches} deteções revistas`} />
      </SimpleGrid>

      {!completed.length ? (
        <Alert color="blue">Ainda não há análises concluídas neste caso.</Alert>
      ) : (
        <>
          <Card withBorder>
            <Group justify="space-between" mb="xs">
              <Title order={5}>Progresso da revisão</Title>
              <Legend />
            </Group>
            {r.matches === 0 ? <Text c="dimmed" size="sm">Sem correspondências nas análises escolhidas.</Text> : (
              <>
                <Progress.Root size={18} radius="sm" style={{ gap: 2, background: "transparent" }}>
                  {ORDER.filter((k) => r[k] > 0).map((k) => (
                    <Tooltip key={k} label={`${STATUS[k].label}: ${r[k]} (${Math.round((r[k] / r.matches) * 100)}%)`}>
                      <Progress.Section value={(r[k] / r.matches) * 100} color={STATUS[k].color} />
                    </Tooltip>
                  ))}
                </Progress.Root>
                <Text size="xs" c="dimmed" mt={6}>
                  {r.confirmed} confirmadas · {r.rejected} rejeitadas · {r.pending} por rever
                  {" "}(tracks: {r.tracks_confirmed} confirmados, {r.tracks_rejected} rejeitados, {r.tracks_pending} por rever)
                </Text>
              </>
            )}
          </Card>

          {data.per_location.length > 0 && (
            <Card withBorder>
              <Group justify="space-between" mb="xs">
                <Title order={5}>Por local</Title>
                <Anchor size="xs" component="button" onClick={() => onOpenTab("videos")}>Locais e vídeos</Anchor>
              </Group>
              <Table verticalSpacing={6}>
                <Table.Tbody>
                  {data.per_location.map((l) => (
                    <Table.Tr key={l.location_id}>
                      <Table.Td w="30%">
                        <Text size="sm" fw={600} truncate="end" title={l.name}>{l.name}</Text>
                        <Text size="xs" c="dimmed">{l.cameras} câmara{l.cameras === 1 ? "" : "s"}</Text>
                      </Table.Td>
                      <Table.Td>{l.matches ? <StackedBar counts={l} max={maxLoc} />
                        : <Text size="xs" c="dimmed">sem correspondências</Text>}</Table.Td>
                      <Table.Td w={64} ta="right">
                        <Text size="sm" fw={600}>{l.matches}</Text>
                        {l.clothing > 0 && <Text size="xs" c="dimmed">+{l.clothing} roupa</Text>}
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Card>
          )}

          <SimpleGrid cols={{ base: 1, lg: 2 }}>
            <Card withBorder>
              <Group justify="space-between" mb="xs">
                <Title order={5}>Por câmara</Title>
                <Text size="xs" c="dimmed">correspondências</Text>
              </Group>
              {data.per_camera.length === 0 ? <Text c="dimmed" size="sm">Sem dados.</Text> : (
                <Table verticalSpacing={6} highlightOnHover>
                  <Table.Tbody>
                    {data.per_camera.map((c) => (
                      <Table.Tr key={c.video_id} style={{ cursor: c.job_ids.length ? "pointer" : undefined }}
                        onClick={() => c.job_ids.length && navigate(`/jobs/${c.job_ids[0]}?video=${c.video_id}&t=${c.first_s ?? 0}`)}>
                        <Table.Td w="38%">
                          <Text size="sm" truncate="end" title={c.filename}>{c.camera_label || c.filename}</Text>
                          <Text size="xs" c="dimmed" ff="monospace">
                            {c.first ? `${c.first.slice(11, 19)} → ${c.last?.slice(11, 19)}`
                              : c.first_s != null ? `vídeo ${formatTime(c.first_s)} → ${formatTime(c.last_s ?? 0)}` : "—"}
                            {!c.synced && <Badge ml={6} size="xs" variant="light" color="gray">sem hora</Badge>}
                          </Text>
                        </Table.Td>
                        <Table.Td><StackedBar counts={c} max={maxCam} /></Table.Td>
                        <Table.Td w={64} ta="right">
                          <Text size="sm" fw={600}>{c.matches}</Text>
                          {c.clothing > 0 && <Text size="xs" c="dimmed">+{c.clothing} roupa</Text>}
                        </Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              )}
            </Card>

            <Card withBorder>
              <Group justify="space-between" mb="xs">
                <Title order={5}>Por hora real</Title>
                <Anchor size="xs" component="button" onClick={() => onOpenTab("route")}>Ver percurso</Anchor>
              </Group>
              {data.per_hour.length === 0
                ? <Text c="dimmed" size="sm">Sem correspondências em vídeos com hora real acertada.</Text>
                : <HourHistogram data={data.per_hour} />}
              {data.unsynced_matches > 0 && (
                <Text size="xs" c="dimmed" mt="xs">
                  {data.unsynced_matches} correspondência(s) em vídeos sem hora real não aparecem aqui
                  {" "}(acerte o relógio no separador Locais e vídeos).
                </Text>
              )}
            </Card>
          </SimpleGrid>

          {data.per_person.length > 1 && (
            <Card withBorder>
              <Title order={5} mb="xs">Por pessoa procurada</Title>
              <Stack gap={6}>
                {data.per_person.map((p) => (
                  <Group key={p.label} wrap="nowrap" gap="sm">
                    <Text size="sm" w={160} truncate="end">{p.label}</Text>
                    <Tooltip label={`${p.label}: ${p.matches} correspondência(s), ${p.confirmed} confirmada(s)`}>
                      <Box style={{ height: 14, width: `${(p.matches / maxPerson) * 60}%`, minWidth: 4, borderRadius: 4,
                        background: `var(--mantine-color-${personColor(personOrder.indexOf(p.label) + 1)}-6)` }} />
                    </Tooltip>
                    <Text size="sm" fw={600}>{p.matches}</Text>
                    <Text size="xs" c="dimmed">({p.confirmed} confirmadas)</Text>
                  </Group>
                ))}
              </Stack>
            </Card>
          )}

          {data.discover.jobs > 0 && (
            <Text size="sm" c="dimmed">
              Modo descoberta: {data.discover.jobs} análise(s) · {data.discover.faces} faces · {data.discover.persons} pessoas distintas
              {" "}(não contam como correspondências).
            </Text>
          )}
        </>
      )}

      <Card withBorder>
        <Group justify="space-between" mb="xs">
          <Title order={5}>Atividade recente</Title>
          <Anchor size="xs" component="button" onClick={() => onOpenTab("audit")}>Ver auditoria completa</Anchor>
        </Group>
        {data.recent.length === 0 ? <Text size="sm" c="dimmed">Sem eventos.</Text> : (
          <Stack gap={4}>
            {data.recent.map((e) => (
              <Group key={e.id} gap="xs" wrap="nowrap">
                <Text size="xs" c="dimmed" ff="monospace" w={150}>{formatDate(e.timestamp)}</Text>
                <Text size="xs" fw={600}>{e.actor || "—"}</Text>
                <Text size="xs">{ACTIONS[e.action] ?? e.action}</Text>
              </Group>
            ))}
          </Stack>
        )}
      </Card>
    </Stack>
  );
}
