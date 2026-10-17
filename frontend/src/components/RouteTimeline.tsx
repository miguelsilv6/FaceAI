import {
  Alert, Anchor, Badge, Box, Button, Card, Group, MultiSelect, Select, Stack, Switch, Table, Text, Tooltip,
} from "@mantine/core";
import { IconDownload, IconMapPin } from "@tabler/icons-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api, formatTime } from "../api";
import { notifyError } from "../notify";
import { personColor } from "../personColors";
import type { CaseRoute, Job, Location, RouteSegment, Video } from "../types";
import MultiCamPlayer, { type SeekRequest } from "./MultiCamPlayer";

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
  const rows = [["hora_real_inicio", "hora_real_fim", "local", "camara", "ficheiro", "pessoa", "analise", "tempo_video_inicio",
    "tempo_video_fim", "evidencia", "semelhanca", "deteccao"]];
  for (const s of segs) {
    rows.push([s.real_start ?? "", s.real_end ?? "", s.location_name, s.camera_name, s.video_filename, s.person_label, String(s.job_id),
      formatTime(s.start_s), formatTime(s.end_s), s.evidence, s.best_similarity?.toFixed(4) ?? "",
      String(s.best_detection_id ?? "")]);
  }
  return "﻿" + rows.map((r) => r.map((c) => `"${c.replace(/"/g, '""')}"`).join(",")).join("\n");
}

/** Percurso da(s) pessoa(s) entre câmaras, pela hora real do DVR (linhas = câmaras/vídeos). */
export default function RouteTimeline({ caseId, jobs, videos, locations }: {
  caseId: number; jobs: Job[]; videos: Video[]; locations: Location[];
}) {
  const navigate = useNavigate();
  const completed = jobs.filter((j) => j.status === "completed");
  const [jobIds, setJobIds] = useState<string[]>([]);
  const [confirmedOnly, setConfirmedOnly] = useState(false);
  const [route, setRoute] = useState<CaseRoute | null>(null);
  const [seek, setSeek] = useState<SeekRequest | null>(null);
  const [onlyLoc, setOnlyLoc] = useState<string | null>(null); // "Só este local"
  // Informação "sem hora real" escondida (preferência deste caso, guardada no browser)
  const hideKey = `faceai.route.hideUnsynced.${caseId}`;
  const [hideUnsynced, setHideUnsyncedState] = useState(() => {
    try { return localStorage.getItem(hideKey) === "1"; } catch { return false; }
  });
  const setHideUnsynced = (v: boolean) => {
    setHideUnsyncedState(v);
    try { if (v) localStorage.setItem(hideKey, "1"); else localStorage.removeItem(hideKey); } catch { /* sem storage */ }
  };
  const playerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Por omissão, todas as análises "procurar" concluídas: cada câmara pode ter sido analisada à parte.
    if (!jobIds.length && completed.length) {
      const match = completed.filter((j) => j.params.mode !== "discover");
      setJobIds((match.length ? match : completed.slice(0, 1)).map((j) => String(j.id)));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [completed.length]);

  useEffect(() => {
    if (!jobIds.length) { setRoute(null); return; }
    api.route(caseId, jobIds.map(Number), confirmedOnly ? "confirmed" : "matches").then(setRoute).catch(notifyError);
  }, [caseId, jobIds, confirmedOnly]);

  const inLoc = <T extends { location_id: number | null }>(x: T) => !onlyLoc || String(x.location_id) === onlyLoc;
  const segs = useMemo(() => (route?.segments ?? []).filter(inLoc),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [route, onlyLoc]);
  const routeVideos = useMemo(() => (route?.videos ?? []).filter(inLoc),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [route, onlyLoc]);
  const keys = useMemo(() => [...new Set([...segs, ...(route?.unsynced ?? [])]
    .map((s) => `${s.job_id}:${s.person_id ?? 0}`))], [route]);
  // Uma linha por câmara com hora acertada, agrupadas por local (pela ordem do 1.º aparecimento em cada local):
  // primeiro as câmaras com aparições (pela ordem do percurso), depois as vazias.
  const lanes = useMemo(() => {
    const ids: number[] = [];
    for (const s of segs) if (!ids.includes(s.video_id)) ids.push(s.video_id);
    for (const v of routeVideos) if (v.synced && !ids.includes(v.id)) ids.push(v.id);
    const info = new Map(routeVideos.map((v) => [v.id, v]));
    const all = ids.map((id) => {
      const v = info.get(id);
      const seg = segs.find((x) => x.video_id === id);
      return { id, camera: v?.camera_name ?? seg?.camera_name ?? `#${id}`, filename: v?.filename ?? seg?.video_filename ?? "",
        locationId: v?.location_id ?? seg?.location_id ?? null, location: v?.location_name ?? seg?.location_name ?? "" };
    });
    const order = [...new Set(all.map((l) => l.locationId))];
    return order.flatMap((loc) => all.filter((l) => l.locationId === loc));
  }, [segs, routeVideos]);
  const locOptions = (route?.locations ?? []).filter((l) => (route?.videos ?? []).some((v) => v.location_id === l.id));
  const camerasWithSegs = new Set(segs.map((s) => s.video_id)).size;
  const t0 = segs.length ? Math.min(...segs.map((s) => seconds(s.real_start!))) : 0;
  const t1 = segs.length ? Math.max(...segs.map((s) => seconds(s.real_end ?? s.real_start!))) : 1;
  const span = Math.max(1, t1 - t0);
  const pct = (t: number) => ((t - t0) / span) * 100;
  const open = (s: RouteSegment) => navigate(`/jobs/${s.job_id}?video=${s.video_id}&t=${s.start_s}`);
  // Leva todas as câmaras do leitor para 2 s antes da aparição.
  const watch = (s: RouteSegment) => {
    setSeek((q) => ({ wall: seconds(s.real_start!) - 2, n: (q?.n ?? 0) + 1 }));
    playerRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  const player = (
    <div ref={playerRef}>
      <MultiCamPlayer videos={onlyLoc ? videos.filter((v) => String(v.location_id) === onlyLoc) : videos}
        locations={locations} segments={segs} seek={seek} />
    </div>
  );
  const unsyncedVideos = routeVideos.filter((v) => !v.synced && v.appearances > 0);

  const download = () => {
    const url = URL.createObjectURL(new Blob([toCsv(segs)], { type: "text/csv" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `faceai_caso${caseId}_percurso.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  if (!completed.length) {
    return <Stack><Text c="dimmed">Ainda não há análises concluídas neste caso.</Text>{player}</Stack>;
  }
  return (
    <Stack>
      <Group align="flex-end" wrap="wrap">
        <MultiSelect label="Análises" w={360} value={jobIds} onChange={setJobIds}
          data={completed.map((j) => ({ value: String(j.id),
            label: `#${j.id} · ${j.params.mode === "discover" ? "descoberta" : (j.params.persons ?? []).map((p) => p.label).join(", ") || "procurar"}` }))} />
        {locOptions.length > 1 && (
          <Select label="Local" w={220} clearable placeholder="Todos os locais" value={onlyLoc} onChange={setOnlyLoc}
            leftSection={<IconMapPin size={14} />}
            data={locOptions.map((l) => ({ value: String(l.id), label: `Só ${l.name}` }))} />
        )}
        <Switch label="Só deteções confirmadas" checked={confirmedOnly}
          onChange={(e) => setConfirmedOnly(e.currentTarget.checked)} />
        <Button variant="light" size="xs" leftSection={<IconDownload size={16} />} disabled={!segs.length}
          onClick={download}>CSV</Button>
      </Group>
      <Text size="sm" c="dimmed">Cada linha é um vídeo, agrupados por local; os blocos são as aparições, pela hora real do DVR.
        Blocos tracejados vêm só do vestuário. Clique num bloco para ver todos os vídeos nesse instante (leitor abaixo).</Text>
      {unsyncedVideos.length > 0 && !hideUnsynced && (
        <Alert color="yellow" withCloseButton closeButtonLabel="Esconder" onClose={() => setHideUnsynced(true)}>
          {unsyncedVideos.length} vídeo(s) com aparições mas sem hora real acertada ({unsyncedVideos.map((v) => v.camera_label).join(", ")}):
          ficam fora da linha temporal. Acerte o relógio no separador <b>Locais e vídeos</b> (botão "Acertar hora").
        </Alert>
      )}
      {route && segs.length > 0 && camerasWithSegs < 2 && (
        <Alert color="blue">
          Só {lanes.length > 1 ? "um dos vídeos tem" : "há um vídeo com"} aparições nas análises escolhidas.
          {lanes.length > 1
            ? " Os outros vídeos foram analisados mas a pessoa não foi encontrada neles (ou só abaixo do limiar)."
            : " Para ver o percurso entre vídeos, inclua os outros vídeos na análise (ou escolha também as análises desses vídeos no campo Análises) e acerte a hora de cada vídeo."}
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
            {lanes.map((lane, li) => (
              <Box key={lane.id}>
              {(li === 0 || lanes[li - 1].locationId !== lane.locationId) && lane.location && (
                <Group gap={4} mt={li ? 6 : 0} py={2} style={{ borderTop: "2px solid var(--mantine-color-indigo-3)" }}>
                  <IconMapPin size={14} color="var(--mantine-color-indigo-6)" />
                  <Text size="xs" fw={700} c="indigo.7">{lane.location}</Text>
                </Group>
              )}
              <Group gap={0} wrap="nowrap" h={LANE_H}
                style={{ borderTop: "1px solid var(--mantine-color-default-border)" }}>
                <Text size="xs" w={LABEL_W} pr="xs" pl="xs" truncate="end" title={`${lane.location} · ${lane.camera} (${lane.filename})`}>{lane.camera}</Text>
                <Box style={{ position: "relative", flex: 1, height: LANE_H }}>
                  {!segs.some((s) => s.video_id === lane.id) && (
                    <Text size="xs" c="dimmed" lh={`${LANE_H}px`} pl="xs">sem aparições</Text>
                  )}
                  {segs.filter((s) => s.video_id === lane.id).map((s, i) => {
                    const left = pct(seconds(s.real_start!));
                    const width = Math.max(0.6, pct(seconds(s.real_end ?? s.real_start!)) - left);
                    const color = `var(--mantine-color-${colorKey(s, keys)}-6)`;
                    return (
                      <Tooltip key={i} label={`${s.person_label} · ${hhmmss(s.real_start!)}–${hhmmss(s.real_end ?? s.real_start!)}`
                        + ` · ${s.evidence}${s.best_similarity != null ? ` · ${(s.best_similarity * 100).toFixed(0)}%` : ""}`}>
                        <Box component="button" aria-label={`${s.person_label} em ${s.camera_label} às ${hhmmss(s.real_start!)}`}
                          onClick={() => watch(s)}
                          style={{ position: "absolute", left: `${left}%`, width: `${width}%`, top: 7, height: LANE_H - 14,
                            background: s.evidence === "vestuário" ? "transparent" : color, cursor: "pointer",
                            border: `2px ${s.evidence === "vestuário" ? "dashed" : "solid"} ${color}`, borderRadius: 4, padding: 0 }} />
                      </Tooltip>
                    );
                  })}
                </Box>
              </Group>
              </Box>
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
      {player}
      {segs.length > 0 && (
        <Card withBorder p={0}>
          <Table.ScrollContainer minWidth={640}>
            <Table striped verticalSpacing={4}>
              <Table.Thead>
                <Table.Tr><Table.Th>Hora real</Table.Th><Table.Th>Vídeo</Table.Th><Table.Th>Pessoa</Table.Th>
                  <Table.Th>No vídeo</Table.Th><Table.Th>Evidência</Table.Th><Table.Th /></Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {segs.map((s, i) => (
                  <Table.Tr key={i}>
                    <Table.Td ff="monospace">{hhmmss(s.real_start!)}–{hhmmss(s.real_end ?? s.real_start!)}</Table.Td>
                    <Table.Td title={s.video_filename}>{s.camera_label}</Table.Td>
                    <Table.Td><Badge color={colorKey(s, keys)} variant="light">{s.person_label}</Badge></Table.Td>
                    <Table.Td ff="monospace">{formatTime(s.start_s)}</Table.Td>
                    <Table.Td>{s.evidence}</Table.Td>
                    <Table.Td>
                      <Group gap="xs" wrap="nowrap">
                        <Anchor component="button" size="xs" onClick={() => watch(s)}>Ver vídeos</Anchor>
                        <Anchor component="button" size="xs" onClick={() => open(s)}>Resultados</Anchor>
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </Card>
      )}
      {route && route.unsynced.filter(inLoc).length > 0 && (hideUnsynced ? (
        <Anchor size="xs" component="button" c="dimmed" onClick={() => setHideUnsynced(false)}>
          Mostrar aparições sem hora real ({route.unsynced.filter(inLoc).length})
        </Anchor>
      ) : (
        <Group gap={6} wrap="nowrap" align="flex-start">
          <Text size="xs" c="dimmed">Sem hora real: {route.unsynced.filter(inLoc).map((s) => `${s.camera_label} ${formatTime(s.start_s)}`).join(" · ")}.{" "}
            <Anchor component={Link} size="xs" to={`/jobs/${route.unsynced[0].job_id}`}>Ver resultados</Anchor></Text>
          <Anchor size="xs" component="button" onClick={() => setHideUnsynced(true)}>Esconder</Anchor>
        </Group>
      ))}
    </Stack>
  );
}
