import {
  Alert, Anchor, Badge, Box, Button, Card, Group, MultiSelect, Stack, Switch, Table, Text, Tooltip,
} from "@mantine/core";
import { IconDownload } from "@tabler/icons-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api, formatTime } from "../api";
import { notifyError } from "../notify";
import { personColor } from "../personColors";
import type { CaseRoute, Job, RouteSegment } from "../types";

const LANE_H = 34;
const LABEL_W = 170;

function hhmmss(iso: string): string {
  return iso.slice(11, 19);
}

function seconds(iso: string): number {
  return new Date(iso + (iso.endsWith("Z") ? "" : "Z")).getTime() / 1000; // hora "de parede" do DVR, sem fuso
}

/** Cor por (análise, pessoa): estável dentro da vista. */
function colorKey(s: RouteSegment, keys: string[]): string {
  const k = `${s.job_id}:${s.person_id ?? 0}`;
  return personColor(keys.indexOf(k) + 1);
}

function toCsv(segs: RouteSegment[]): string {
  const rows = [["hora_real_inicio", "hora_real_fim", "camara", "pessoa", "analise", "tempo_video_inicio",
    "tempo_video_fim", "evidencia", "semelhanca", "deteccao"]];
  for (const s of segs) {
    rows.push([s.real_start ?? "", s.real_end ?? "", s.video_filename, s.person_label, String(s.job_id),
      formatTime(s.start_s), formatTime(s.end_s), s.evidence, s.best_similarity?.toFixed(4) ?? "",
      String(s.best_detection_id ?? "")]);
  }
  return "﻿" + rows.map((r) => r.map((c) => `"${c.replace(/"/g, '""')}"`).join(",")).join("\n");
}

/** Percurso da(s) pessoa(s) entre câmaras, pela hora real do DVR (linhas = câmaras/vídeos). */
export default function RouteTimeline({ caseId, jobs }: { caseId: number; jobs: Job[] }) {
  const navigate = useNavigate();
  const completed = jobs.filter((j) => j.status === "completed");
  const [jobIds, setJobIds] = useState<string[]>([]);
  const [confirmedOnly, setConfirmedOnly] = useState(false);
  const [route, setRoute] = useState<CaseRoute | null>(null);

  useEffect(() => {
    if (!jobIds.length && completed.length) setJobIds([String(completed[0].id)]); // mais recente
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [completed.length]);

  useEffect(() => {
    if (!jobIds.length) { setRoute(null); return; }
    api.route(caseId, jobIds.map(Number), confirmedOnly ? "confirmed" : "matches").then(setRoute).catch(notifyError);
  }, [caseId, jobIds, confirmedOnly]);

  const segs = route?.segments ?? [];
  const keys = useMemo(() => [...new Set([...segs, ...(route?.unsynced ?? [])]
    .map((s) => `${s.job_id}:${s.person_id ?? 0}`))], [route]);
  const lanes = useMemo(() => {
    const ids: number[] = [];
    for (const s of segs) if (!ids.includes(s.video_id)) ids.push(s.video_id);
    return ids.map((id) => ({ id, filename: segs.find((s) => s.video_id === id)!.video_filename }));
  }, [segs]);
  const t0 = segs.length ? Math.min(...segs.map((s) => seconds(s.real_start!))) : 0;
  const t1 = segs.length ? Math.max(...segs.map((s) => seconds(s.real_end ?? s.real_start!))) : 1;
  const span = Math.max(1, t1 - t0);
  const pct = (t: number) => ((t - t0) / span) * 100;
  const open = (s: RouteSegment) => navigate(`/jobs/${s.job_id}?video=${s.video_id}&t=${s.start_s}`);
  const unsyncedVideos = route?.videos.filter((v) => !v.synced) ?? [];

  const download = () => {
    const url = URL.createObjectURL(new Blob([toCsv(segs)], { type: "text/csv" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `faceai_caso${caseId}_percurso.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  if (!completed.length) return <Text c="dimmed">Ainda não há análises concluídas neste caso.</Text>;
  return (
    <Stack>
      <Group align="flex-end" wrap="wrap">
        <MultiSelect label="Análises" w={360} value={jobIds} onChange={setJobIds}
          data={completed.map((j) => ({ value: String(j.id),
            label: `#${j.id} · ${j.params.mode === "discover" ? "descoberta" : (j.params.persons ?? []).map((p) => p.label).join(", ") || "procurar"}` }))} />
        <Switch label="Só deteções confirmadas" checked={confirmedOnly}
          onChange={(e) => setConfirmedOnly(e.currentTarget.checked)} />
        <Button variant="light" size="xs" leftSection={<IconDownload size={16} />} disabled={!segs.length}
          onClick={download}>CSV</Button>
      </Group>
      <Text size="sm" c="dimmed">Cada linha é uma câmara (vídeo); os blocos são as aparições, pela hora real do DVR.
        Blocos tracejados vêm só do vestuário. Clique num bloco para abrir os resultados nesse instante.</Text>
      {unsyncedVideos.length > 0 && (
        <Alert color="yellow">
          {unsyncedVideos.length} vídeo(s) com aparições mas sem hora real acertada ({unsyncedVideos.map((v) => v.filename).join(", ")}):
          ficam fora da linha temporal. Acerte o relógio no separador <b>Vídeos</b> (botão do relógio).
        </Alert>
      )}
      {segs.length === 0 ? (
        <Text c="dimmed">Sem aparições {confirmedOnly ? "confirmadas " : ""}com hora real nas análises escolhidas.</Text>
      ) : (
        <Card withBorder>
          <Box style={{ position: "relative", minWidth: 600 }}>
            <Group justify="space-between" ml={LABEL_W} mb={4}>
              <Text size="xs" ff="monospace">{hhmmss(segs[0].real_start!)}</Text>
              <Text size="xs" c="dimmed">{segs[0].real_start!.slice(0, 10)}</Text>
              <Text size="xs" ff="monospace">{new Date(t1 * 1000).toISOString().slice(11, 19)}</Text>
            </Group>
            {lanes.map((lane) => (
              <Group key={lane.id} gap={0} wrap="nowrap" h={LANE_H}
                style={{ borderTop: "1px solid var(--mantine-color-default-border)" }}>
                <Text size="xs" w={LABEL_W} pr="xs" truncate="end" title={lane.filename}>{lane.filename}</Text>
                <Box style={{ position: "relative", flex: 1, height: LANE_H }}>
                  {segs.filter((s) => s.video_id === lane.id).map((s, i) => {
                    const left = pct(seconds(s.real_start!));
                    const width = Math.max(0.6, pct(seconds(s.real_end ?? s.real_start!)) - left);
                    const color = `var(--mantine-color-${colorKey(s, keys)}-6)`;
                    return (
                      <Tooltip key={i} label={`${s.person_label} · ${hhmmss(s.real_start!)}–${hhmmss(s.real_end ?? s.real_start!)}`
                        + ` · ${s.evidence}${s.best_similarity != null ? ` · ${(s.best_similarity * 100).toFixed(0)}%` : ""}`}>
                        <Box component="button" aria-label={`${s.person_label} em ${lane.filename} às ${hhmmss(s.real_start!)}`}
                          onClick={() => open(s)}
                          style={{ position: "absolute", left: `${left}%`, width: `${width}%`, top: 7, height: LANE_H - 14,
                            background: s.evidence === "vestuário" ? "transparent" : color, cursor: "pointer",
                            border: `2px ${s.evidence === "vestuário" ? "dashed" : "solid"} ${color}`, borderRadius: 4, padding: 0 }} />
                      </Tooltip>
                    );
                  })}
                </Box>
              </Group>
            ))}
          </Box>
          <Group gap="xs" mt="sm">
            {keys.map((k, i) => {
              const s = [...segs, ...(route?.unsynced ?? [])].find((x) => `${x.job_id}:${x.person_id ?? 0}` === k)!;
              return <Badge key={k} color={personColor(i + 1)} variant="light">{s.person_label}{jobIds.length > 1 ? ` · #${s.job_id}` : ""}</Badge>;
            })}
          </Group>
        </Card>
      )}
      {segs.length > 0 && (
        <Card withBorder p={0}>
          <Table.ScrollContainer minWidth={640}>
            <Table striped verticalSpacing={4}>
              <Table.Thead>
                <Table.Tr><Table.Th>Hora real</Table.Th><Table.Th>Câmara</Table.Th><Table.Th>Pessoa</Table.Th>
                  <Table.Th>No vídeo</Table.Th><Table.Th>Evidência</Table.Th><Table.Th /></Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {segs.map((s, i) => (
                  <Table.Tr key={i}>
                    <Table.Td ff="monospace">{hhmmss(s.real_start!)}–{hhmmss(s.real_end ?? s.real_start!)}</Table.Td>
                    <Table.Td>{s.video_filename}</Table.Td>
                    <Table.Td><Badge color={colorKey(s, keys)} variant="light">{s.person_label}</Badge></Table.Td>
                    <Table.Td ff="monospace">{formatTime(s.start_s)}</Table.Td>
                    <Table.Td>{s.evidence}</Table.Td>
                    <Table.Td>
                      <Anchor component="button" size="xs" onClick={() => open(s)}>Abrir</Anchor>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </Card>
      )}
      {route && route.unsynced.length > 0 && (
        <Text size="xs" c="dimmed">Sem hora real: {route.unsynced.map((s) => `${s.video_filename} ${formatTime(s.start_s)}`).join(" · ")}.{" "}
          <Anchor component={Link} size="xs" to={`/jobs/${route.unsynced[0].job_id}`}>Ver resultados</Anchor></Text>
      )}
    </Stack>
  );
}
