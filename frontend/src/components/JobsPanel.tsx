import {
  Accordion, ActionIcon, Alert, Badge, Button, Card, Checkbox, Chip, Group, Input, NumberInput, Progress, SegmentedControl, Select, SimpleGrid, Stack,
  Menu, Switch, Table, Text, Title, Tooltip,
} from "@mantine/core";
import { IconDownload, IconInfoCircle, IconMapPin, IconPlayerPlay, IconTrash } from "@tabler/icons-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, formatDate, formatEta } from "../api";
import { notifyError, notifyOk } from "../notify";
import { cameraName } from "../places";
import type { CasePerson, Job, JobMode, JobParams, Location, ReferenceImage, SystemInfo, Video } from "../types";
import DeleteJobModal from "./DeleteJobModal";
import JobStatusBadge from "./JobStatusBadge";
import ReferencePicker, { allChoices, choicesToRefIds, type RefChoice } from "./ReferencePicker";

interface Props {
  caseId: number;
  jobs: Job[];
  videos: Video[];
  locations: Location[];
  references: ReferenceImage[];
  persons: CasePerson[];
  onChange: () => void;
}

export default function JobsPanel({ caseId, jobs, videos, locations, references, persons, onChange }: Props) {
  const navigate = useNavigate();
  const downloadValidated = async () => {
    try {
      const sum = await api.validatedSummary(caseId);
      if (!sum.detections) {
        notifyError(new Error("Ainda não há deteções confirmadas neste caso. Valide-as nos resultados das análises."));
        return;
      }
      notifyOk(`A preparar ${sum.frames} frame(s) de ${sum.detections} deteção(ões) confirmada(s)…`, "Download");
      window.location.href = api.validatedFramesUrl("cases", caseId);
    } catch (e) {
      notifyError(e);
    }
  };
  const [system, setSystem] = useState<SystemInfo | null>(null);
  const [params, setParams] = useState<JobParams | null>(null);
  // Locais escolhidos (todas as câmaras de cada um) e câmaras retiradas individualmente
  const [locIds, setLocIds] = useState<string[]>([]);
  const [excluded, setExcluded] = useState<number[]>([]);
  const [choices, setChoices] = useState<RefChoice[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [toDelete, setToDelete] = useState<Job | null>(null);
  const validRefs = useMemo(() => references.filter((r) => r.valid), [references]);

  useEffect(() => {
    api.system().then((s) => {
      setSystem(s);
      const threshold = s.models.find((m) => m.name === s.defaults.model)?.default_threshold ?? null;
      setParams({ ...s.defaults, threshold });
    }).catch(notifyError);
  }, []);
  const withVideos = useMemo(() => locations.filter((l) => videos.some((v) => v.location_id === l.id)), [locations, videos]);
  const locKey = withVideos.map((l) => l.id).join(",");
  useEffect(() => {
    setLocIds(withVideos.map((l) => String(l.id)));
    setExcluded([]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [locKey]);
  const chosenVideos = useMemo(() => videos.filter((v) => locIds.includes(String(v.location_id))
    && !excluded.includes(v.id)), [videos, locIds, excluded]);
  const videoIds = chosenVideos.map((v) => v.id);
  const nLocs = new Set(chosenVideos.map((v) => v.location_id)).size;
  useEffect(() => setChoices(allChoices(validRefs, persons)), [validRefs, persons]);
  const refIds = useMemo(() => choicesToRefIds(choices, validRefs), [choices, validRefs]);
  // Sem nenhuma amostra válida, o modo por omissão é a descoberta de todas as faces.
  const [modeTouched, setModeTouched] = useState(false);
  useEffect(() => {
    if (!modeTouched) setParams((p) => (p ? { ...p, mode: validRefs.length ? "match" : "discover" } : p));
  }, [validRefs, modeTouched, system]);
  const discover = params?.mode === "discover";

  // Atualiza a lista enquanto houver análises a decorrer.
  const active = jobs.some((j) => j.status === "running" || j.status === "queued");
  useEffect(() => {
    if (!active) return;
    const t = setInterval(onChange, 1500);
    return () => clearInterval(t);
  }, [active, onChange]);

  const set = <K extends keyof JobParams>(key: K, value: JobParams[K]) => setParams((p) => (p ? { ...p, [key]: value } : p));

  const start = async () => {
    if (!params) return;
    setSubmitting(true);
    try {
      // Sem câmaras retiradas, a análise é pedida por locais (fica registada assim); senão, câmara a câmara.
      const job = await api.createJob(caseId, params, videoIds, discover ? [] : refIds,
        excluded.length ? undefined : locIds.map(Number));
      navigate(`/jobs/${job.id}`);
    } catch (e) {
      notifyError(e, "Não foi possível iniciar a análise");
    } finally {
      setSubmitting(false);
    }
  };

  const num = (v: string | number, fallback: number) => (typeof v === "number" ? v : Number(v) || fallback);

  return (
    <Stack>
      <Card withBorder>
        <Title order={4} mb="sm">Nova análise</Title>
        {!params || !system ? (
          <Text c="dimmed">A carregar configuração…</Text>
        ) : (
          <Stack>
            <SegmentedControl value={params.mode} fullWidth
              onChange={(m) => { setModeTouched(true); set("mode", m as JobMode); }}
              data={[{ value: "match", label: "Procurar por pessoa(s) - com amostra" },
                { value: "discover", label: "Descobrir todas as faces (sem amostra)" }]} />
            {discover && (
              <Alert color="blue" icon={<IconInfoCircle size={18} />}>
                Todas as faces detetadas são seguidas e agrupadas automaticamente em <b>pessoas distintas</b>. No fim
                pode rever cada pessoa, juntar ou separar grupos e usar uma face como amostra para procurar essa pessoa
                noutros vídeos.
              </Alert>
            )}
            <SimpleGrid cols={{ base: 1, md: discover ? 1 : 2 }}>
              <Input.Wrapper label="Locais" description={
                `${discover ? "Todas as faces" : personsCount(choices)} × ${nLocs} ${nLocs === 1 ? "local" : "locais"} (${videoIds.length} câmara${videoIds.length === 1 ? "" : "s"})`}>
                <Stack gap={6} mt={6}>
                  {withVideos.length === 0 ? (
                    <Text size="sm" c="dimmed">Sem vídeos: importe-os no separador "Locais e vídeos".</Text>
                  ) : (
                    <Chip.Group multiple value={locIds} onChange={setLocIds}>
                      <Group gap={6}>
                        {withVideos.map((l) => {
                          const n = videos.filter((v) => v.location_id === l.id).length;
                          return (
                            <Chip key={l.id} value={String(l.id)} size="sm" icon={<IconMapPin size={12} />}>
                              {l.name} · {n} câmara{n === 1 ? "" : "s"}
                            </Chip>
                          );
                        })}
                      </Group>
                    </Chip.Group>
                  )}
                  {locIds.length > 0 && (
                    <Accordion variant="default" chevronPosition="left">
                      <Accordion.Item value="cams">
                        <Accordion.Control py={0}>
                          <Text size="xs">Ajustar câmaras{excluded.length ? ` (${excluded.length} retirada${excluded.length === 1 ? "" : "s"})` : ""}</Text>
                        </Accordion.Control>
                        <Accordion.Panel>
                          <Stack gap={6}>
                            {withVideos.filter((l) => locIds.includes(String(l.id))).map((l) => (
                              <div key={l.id}>
                                <Text size="xs" fw={600}>{l.name}</Text>
                                <Group gap="md">
                                  {videos.filter((v) => v.location_id === l.id).map((v) => (
                                    <Checkbox key={v.id} size="xs" label={cameraName(v)} checked={!excluded.includes(v.id)}
                                      onChange={(e) => {
                                        const on = e.currentTarget.checked;
                                        setExcluded((x) => (on ? x.filter((i) => i !== v.id) : [...x, v.id]));
                                      }} />
                                  ))}
                                </Group>
                              </div>
                            ))}
                          </Stack>
                        </Accordion.Panel>
                      </Accordion.Item>
                    </Accordion>
                  )}
                </Stack>
              </Input.Wrapper>
              {!discover && (
                <ReferencePicker refs={validRefs} persons={persons} value={choices} onChange={setChoices}
                  description={personsSummary(validRefs.filter((r) => refIds.includes(r.id)), persons)}
                  placeholder={validRefs.length ? "Selecione as referências" : "Sem amostras válidas — use o modo de descoberta"} />
              )}
            </SimpleGrid>
            <SimpleGrid cols={{ base: 1, sm: 2, md: 4 }}>
              <Select label="Modelo de reconhecimento" value={params.model} allowDeselect={false}
                data={system.models.map((m) => ({ value: m.name, label: m.description ? `${m.name} — ${m.description}` : m.name }))}
                onChange={(m) => m && setParams({ ...params, model: m,
                  threshold: system.models.find((x) => x.name === m)?.default_threshold ?? params.threshold })} />
              <Select label="Detetor facial" data={system.detectors} value={params.detector} allowDeselect={false}
                onChange={(d) => d && set("detector", d)} />
              <NumberInput label="Frames analisados por segundo" min={0.1} max={60} step={0.5} decimalScale={2}
                value={params.sample_fps} onChange={(v) => set("sample_fps", num(v, 2))} />
              <NumberInput label={discover ? "Limiar de agrupamento" : "Limiar (distância de cosseno)"}
                min={0.01} max={1.99} step={0.01} decimalScale={3}
                description={discover ? "Menor = separa mais pessoas" : "Menor = mais restritivo"} value={params.threshold ?? ""}
                onChange={(v) => set("threshold", num(v, 0.68))} />
            </SimpleGrid>
            <SimpleGrid cols={{ base: 1, sm: 3 }}>
              <Switch label="Pré-filtro de movimento" description="Só analisa frames onde a imagem mudou"
                checked={params.motion_filter} onChange={(e) => set("motion_filter", e.currentTarget.checked)} />
              <Switch label="Cascata de deteção" description="Detetor rápido primeiro; o principal só se houver faces"
                checked={params.cascade} onChange={(e) => set("cascade", e.currentTarget.checked)} />
              <Switch label="Tracking de faces"
                description={discover ? "Obrigatório na descoberta" : "Agrupa a mesma face entre frames"}
                checked={params.tracking || discover} disabled={discover}
                onChange={(e) => set("tracking", e.currentTarget.checked)} />
            </SimpleGrid>
            {!discover && (
              <Card withBorder padding="sm" bg="var(--mantine-color-default-hover)">
                <Switch label="Complementar com vestuário (re-identificação pelo corpo)"
                  description="Usa a roupa da pessoa nos frames em que a face teve correspondência para a procurar noutras câmaras, mesmo de costas ou longe. Evidência complementar: fica sempre por rever."
                  checked={params.clothing} onChange={(e) => set("clothing", e.currentTarget.checked)} />
                {params.clothing && (
                  <SimpleGrid cols={{ base: 1, sm: 2, md: 5 }} mt="sm">
                    <NumberInput label="Janela temporal (± h)" description="Em torno das amostras (hora real); 0 = sem"
                      min={0} step={1} decimalScale={1} value={params.clothing_window_h}
                      onChange={(v) => set("clothing_window_h", num(v, 12))} />
                    <NumberInput label="Limiar ReID (corpo)" description="Menor = mais restritivo; ajustado no caso"
                      min={0.05} max={0.9} step={0.01} decimalScale={3} value={params.reid_threshold ?? ""}
                      onChange={(v) => set("reid_threshold", num(v, 0.27))} />
                    <NumberInput label="Limiar de cor" description="Tronco/pernas: veto de roupa diferente"
                      min={0.05} max={1} step={0.05} decimalScale={2} value={params.color_threshold}
                      onChange={(v) => set("color_threshold", num(v, 0.6))} />
                    <Select label="Detetor de pessoas" data={system.person_detectors ?? ["yolox_s"]}
                      description="yolox_s: preciso · nano: rápido" value={params.person_detector} allowDeselect={false}
                      onChange={(d) => d && set("person_detector", d)} />
                    <NumberInput label="Altura mínima do corpo (px)" min={16} step={10} value={params.min_body_height}
                      onChange={(v) => set("min_body_height", num(v, 80))} />
                  </SimpleGrid>
                )}
              </Card>
            )}
            <Accordion variant="contained">
              <Accordion.Item value="adv">
                <Accordion.Control>Parâmetros avançados</Accordion.Control>
                <Accordion.Panel>
                  <Stack>
                    <SimpleGrid cols={{ base: 1, sm: 2, md: 5 }}>
                      <NumberInput label="Margem de candidatos" description="Guardar para revisão até limiar + margem"
                        disabled={discover} min={0} max={0.5} step={0.01} decimalScale={3} value={params.candidate_margin}
                        onChange={(v) => set("candidate_margin", num(v, 0.08))} />
                      <NumberInput label="Tamanho mínimo da face (px)" min={0} value={params.min_face_size}
                        onChange={(v) => set("min_face_size", num(v, 20))} />
                      <NumberInput label="Confiança mínima do detetor" min={0} max={1} step={0.05} decimalScale={2}
                        value={params.min_confidence} onChange={(v) => set("min_confidence", num(v, 0.5))} />
                      <NumberInput label="Qualidade mínima da face" description="0 = sem filtro"
                        min={0} max={1} step={0.05} decimalScale={2} value={params.min_quality}
                        onChange={(v) => set("min_quality", num(v, 0))} />
                      <NumberInput label="Ampliação da deteção" description="Ajuda com faces pequenas (mais lento)"
                        min={1} max={4} step={0.5} decimalScale={1} value={params.upscale}
                        onChange={(v) => set("upscale", num(v, 1))} />
                    </SimpleGrid>
                    <SimpleGrid cols={{ base: 1, sm: 2, md: 5 }}>
                      <NumberInput label="Limiar de movimento" description="Fração de píxeis alterados"
                        disabled={!params.motion_filter} min={0} max={1} step={0.001} decimalScale={4}
                        value={params.motion_threshold} onChange={(v) => set("motion_threshold", num(v, 0.002))} />
                      <NumberInput label="Frame-chave a cada (s)" description="Analisa mesmo sem movimento"
                        disabled={!params.motion_filter} min={0} step={1} decimalScale={1}
                        value={params.keyframe_interval_s} onChange={(v) => set("keyframe_interval_s", num(v, 5))} />
                      <Select label="Detetor rápido (cascata)" disabled={!params.cascade} data={system.detectors}
                        value={params.fast_detector} allowDeselect={false}
                        onChange={(d) => d && set("fast_detector", d)} />
                      <NumberInput label="Intervalo máx. no track (s)" disabled={!params.tracking && !discover} min={0} step={0.5}
                        decimalScale={1} value={params.track_gap_s} onChange={(v) => set("track_gap_s", num(v, 2))} />
                      <NumberInput label="Intervalo máx. no segmento (s)" min={0} step={0.5} decimalScale={1}
                        value={params.segment_gap_s} onChange={(v) => set("segment_gap_s", num(v, 2))} />
                    </SimpleGrid>
                  </Stack>
                </Accordion.Panel>
              </Accordion.Item>
            </Accordion>
            <Group justify="flex-end">
              <Button leftSection={<IconPlayerPlay size={18} />} onClick={start} loading={submitting}
                disabled={!videoIds.length || (!discover && !refIds.length)}>
                Iniciar análise
              </Button>
            </Group>
          </Stack>
        )}
      </Card>

      {jobs.length > 0 && (
        <Group justify="flex-end">
          <Button variant="light" size="xs" leftSection={<IconDownload size={16} />} onClick={downloadValidated}>
            Descarregar frames originais validados do caso (ZIP)
          </Button>
        </Group>
      )}
      <Card withBorder p={0}>
        {jobs.length === 0 ? (
          <Text c="dimmed" p="md">Ainda não foram feitas análises neste caso.</Text>
        ) : (
          <Table.ScrollContainer minWidth={840}>
            <Table highlightOnHover>
              <Table.Thead>
                <Table.Tr><Table.Th>#</Table.Th><Table.Th>Estado</Table.Th><Table.Th>Progresso</Table.Th>
                  <Table.Th>Locais</Table.Th><Table.Th>Modelo / detetor</Table.Th><Table.Th>Vestuário</Table.Th><Table.Th>Resultado</Table.Th><Table.Th>Criada</Table.Th>
                  <Table.Th>Analista</Table.Th><Table.Th /></Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {jobs.map((j) => (
                  <Table.Tr key={j.id} style={{ cursor: "pointer" }} onClick={() => navigate(`/jobs/${j.id}`)}>
                    <Table.Td>{j.id}</Table.Td>
                    <Table.Td onClick={(e) => j.status === "queued" && e.stopPropagation()}>
                      <Group gap={4} wrap="nowrap">
                        <JobStatusBadge status={j.status} />
                        {j.status === "queued" && <QueueControls job={j} onChange={onChange} />}
                      </Group>
                    </Table.Td>
                    <Table.Td w={200}>
                      <Progress value={j.progress * 100} animated={j.status === "running"} />
                      {j.status === "running" && (
                        <Text size="xs" c="dimmed" mt={2}>{Math.round(j.progress * 100)}% · {formatEta(j.eta_s)}</Text>
                      )}
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm" maw={220} truncate title={jobLocations(j, locations)}>{jobLocations(j, locations) || "—"}</Text>
                      <Text size="xs" c="dimmed">{j.video_ids.length} câmara{j.video_ids.length === 1 ? "" : "s"}</Text>
                    </Table.Td>
                    <Table.Td>{j.params.model} / {j.params.detector}</Table.Td>
                    <Table.Td>
                      {j.params.clothing ? (
                        <Tooltip label={j.status === "completed"
                          ? `${j.clothing_samples} amostra(s) de vestuário · ${j.clothing_summary?.clothing_matches ?? 0} aparição(ões) só pela roupa`
                          : "Análise com o complemento de vestuário (ReID)"}>
                          <Badge size="sm" color="grape" variant="light">Sim</Badge>
                        </Tooltip>
                      ) : <Text size="sm" c="dimmed">—</Text>}
                    </Table.Td>
                    <Table.Td>{j.params.mode === "discover" ? `${j.persons} pessoas (descoberta)` : j.matches}</Table.Td>
                    <Table.Td>{formatDate(j.created_at)}</Table.Td>
                    <Table.Td>{j.created_by}</Table.Td>
                    <Table.Td w={40} onClick={(e) => e.stopPropagation()}>
                      <Tooltip label={j.status === "running" || j.status === "queued"
                        ? "Cancele a análise antes de a eliminar" : "Eliminar análise"}>
                        <ActionIcon variant="subtle" color="red" aria-label={`Eliminar análise ${j.id}`}
                          disabled={j.status === "running" || j.status === "queued"} onClick={() => setToDelete(j)}>
                          <IconTrash size={16} />
                        </ActionIcon>
                      </Tooltip>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        )}
      </Card>
      <DeleteJobModal job={toDelete} onClose={() => setToDelete(null)}
        onDeleted={() => { setToDelete(null); onChange(); }} />
    </Stack>
  );
}

const PRIORITY_LABEL: Record<number, string> = { 1: "alta", 0: "normal", [-1]: "baixa" };

/** Prioridade e retenção de uma análise em fila (a análise a decorrer só pode ser cancelada). */
function QueueControls({ job, onChange }: { job: Job; onChange: () => void }) {
  const update = async (body: { priority?: number; held?: boolean }) => {
    try {
      await api.updateQueue(job.id, body);
      onChange();
    } catch (e) {
      notifyError(e);
    }
  };
  return (
    <Menu position="bottom-start" withinPortal>
      <Menu.Target>
        <Tooltip label="Prioridade e retenção na fila">
          <Badge component="button" variant="light" style={{ cursor: "pointer" }}
            color={job.held ? "gray" : job.priority > 0 ? "red" : job.priority < 0 ? "blue" : "indigo"}>
            {job.held ? "retida" : `prioridade ${PRIORITY_LABEL[job.priority] ?? job.priority}`}
          </Badge>
        </Tooltip>
      </Menu.Target>
      <Menu.Dropdown>
        <Menu.Label>Prioridade</Menu.Label>
        {[1, 0, -1].map((p) => (
          <Menu.Item key={p} disabled={job.priority === p} onClick={() => update({ priority: p })}>
            {PRIORITY_LABEL[p][0].toUpperCase() + PRIORITY_LABEL[p].slice(1)}
          </Menu.Item>
        ))}
        <Menu.Divider />
        <Menu.Item onClick={() => update({ held: !job.held })}>
          {job.held ? "Libertar (pode começar)" : "Reter na fila (não começa)"}
        </Menu.Item>
      </Menu.Dropdown>
    </Menu>
  );
}

/** "2 pessoas na mesma passagem: Suspeito A (3) · Suspeito B (1)". */
function personsSummary(refs: ReferenceImage[], persons: CasePerson[]): string | undefined {
  const groups = new Map<string, number>();
  for (const r of refs) {
    const name = persons.find((p) => p.id === r.case_person_id)?.name ?? "fotos soltas";
    groups.set(name, (groups.get(name) ?? 0) + 1);
  }
  if (groups.size < 2) return undefined;
  return `${groups.size} pessoas na mesma passagem: ${[...groups].map(([l, n]) => `${l} (${n})`).join(" · ")}`;
}

/** "2 pessoas" / "1 pessoa + 2 fotos" a partir das escolhas de amostras. */
function personsCount(choices: RefChoice[]): string {
  const p = choices.filter((c) => c.startsWith("p:")).length;
  const r = choices.length - p;
  const parts = [p ? `${p} pessoa${p === 1 ? "" : "s"}` : "", r ? `${r} foto${r === 1 ? "" : "s"}` : ""].filter(Boolean);
  return parts.join(" + ") || "0 pessoas";
}

/** Locais de uma análise (nome atual; análises antigas sem registo de locais → vazio). */
function jobLocations(j: Job, locations: Location[]): string {
  return (j.params.locations ?? [])
    .map((l) => locations.find((x) => x.id === l.id)?.name ?? l.name)
    .filter(Boolean).join(", ");
}
