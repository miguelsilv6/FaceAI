import {
  ActionIcon, Alert, Anchor, Badge, Box, Button, Card, Checkbox, Chip, Group, Image, Loader, Modal, SegmentedControl,
  Select, SimpleGrid, Stack, Table, Text, Title, Tooltip,
} from "@mantine/core";
import {
  IconChevronLeft, IconChevronRight, IconDownload, IconExternalLink, IconInfoCircle, IconMaximize, IconPhotoDown,
  IconTrash,
} from "@tabler/icons-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, formatDate, formatTime } from "../api";
import { notifyError, notifyOk } from "../notify";
import { jobLocations } from "../places";
import type { Job, Location, ValidatedItem } from "../types";

/** Chave única de um item: deteção confirmada ("d:…") ou frame guardado no leitor ("f:…"). */
const keyOf = (i: ValidatedItem) => (i.type === "saved_frame" ? `f:${i.saved_frame_id}` : `d:${i.detection_id}`);

function imageUrl(i: ValidatedItem, variant: "annotated" | "original" = "annotated") {
  return i.type === "saved_frame" ? api.savedFrameUrl(i.saved_frame_id!) : api.detectionFrameUrl(i.detection_id!, variant);
}

/** Miniatura JPEG reduzida para a grelha (o visualizador e os downloads usam o ficheiro completo). */
function thumbUrl(i: ValidatedItem) {
  return i.type === "saved_frame" ? api.savedFrameUrl(i.saved_frame_id!, false, 360)
    : `${api.detectionImageUrl(i.detection_id!, "frame")}?w=360`;
}

function downloadUrl(i: ValidatedItem) {
  return i.type === "saved_frame" ? api.savedFrameUrl(i.saved_frame_id!, true)
    : api.detectionFrameUrl(i.detection_id!, "original", true);
}

function title(i: ValidatedItem) {
  return i.camera_label || i.video_filename;
}

function when(i: ValidatedItem) {
  return `${i.real_time ? i.real_time.replace("T", " ").slice(0, 19) : "sem hora real"} · vídeo ${formatTime(i.timestamp_s)}`;
}

/** Visualizador das fotos (frames) em tamanho grande, com anterior/seguinte (também ← / →). */
function PhotoViewer({ items, index, onIndex, onClose }: {
  items: ValidatedItem[]; index: number | null; onIndex: (i: number) => void; onClose: () => void;
}) {
  const [variant, setVariant] = useState<"annotated" | "original">("annotated");
  const item = index != null ? items[index] : null;
  const go = useCallback((d: number) => {
    if (index == null || !items.length) return;
    onIndex((index + d + items.length) % items.length);
  }, [index, items.length, onIndex]);
  useEffect(() => {
    if (index == null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowRight") { e.preventDefault(); go(1); }
      if (e.key === "ArrowLeft") { e.preventDefault(); go(-1); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [index, go]);
  if (!item) return null;
  const saved = item.type === "saved_frame";
  return (
    <Modal opened onClose={onClose} size="90%" title={`${title(item)} · ${when(item)}`}>
      <Stack gap="xs">
        <Group justify="space-between" wrap="wrap">
          {saved ? <Badge color="teal" variant="light">Frame guardado no leitor (original)</Badge> : (
            <SegmentedControl size="xs" value={variant} onChange={(v) => setVariant(v as "annotated" | "original")}
              data={[{ value: "annotated", label: "Com marcação" }, { value: "original", label: "Original (sem marcação)" }]} />
          )}
          <Group gap="xs">
            <Text size="xs" c="dimmed">{index! + 1} de {items.length} · ← → para navegar</Text>
            <Button size="xs" variant="light" component="a" href={downloadUrl(item)}
              leftSection={<IconPhotoDown size={14} />}>PNG original</Button>
          </Group>
        </Group>
        <Box style={{ position: "relative", background: "#000", borderRadius: 6 }}>
          <Image loading="lazy" decoding="async" src={imageUrl(item, variant)} fit="contain" mah="72vh" alt={`Frame de ${title(item)}`} />
          {items.length > 1 && <>
            <ActionIcon variant="filled" color="dark" size="xl" radius="xl" aria-label="Foto anterior" onClick={() => go(-1)}
              style={{ position: "absolute", left: 8, top: "50%", transform: "translateY(-50%)", opacity: 0.8 }}>
              <IconChevronLeft />
            </ActionIcon>
            <ActionIcon variant="filled" color="dark" size="xl" radius="xl" aria-label="Foto seguinte" onClick={() => go(1)}
              style={{ position: "absolute", right: 8, top: "50%", transform: "translateY(-50%)", opacity: 0.8 }}>
              <IconChevronRight />
            </ActionIcon>
          </>}
        </Box>
        <Text size="sm">
          Análise #{item.job_id} · frame {item.frame_index}
          {item.person_label ? ` · ${item.person_label}` : ""}
          {!saved && item.similarity != null && item.kind !== "body" ? ` · ${(item.similarity * 100).toFixed(0)}%` : ""}
          {" "}· {saved ? "guardado" : "confirmada"} por {item.reviewed_by || "—"}{item.review_note ? ` · ${item.review_note}` : ""}
        </Text>
      </Stack>
    </Modal>
  );
}

/** Análises concluídas do caso: resumo de cada uma e acesso aos resultados. */
function CompletedJobs({ caseId, jobs, locations, onShowImages, onOpenTab }: {
  caseId: number; jobs: Job[]; locations: Location[]; onShowImages: (jobId: number) => void;
  onOpenTab: (tab: string) => void;
}) {
  const navigate = useNavigate();
  const completed = jobs.filter((j) => j.status === "completed").sort((a, b) => b.id - a.id);
  const running = jobs.filter((j) => j.status === "running" || j.status === "queued").length;
  const [pending, setPending] = useState<Record<number, number>>({});
  const key = jobs.map((j) => `${j.id}:${j.status}`).join(",");
  useEffect(() => {
    api.caseDashboard(caseId, [])
      .then((d) => setPending(Object.fromEntries(d.pending_by_job.map((p) => [p.job_id, p.pending]))))
      .catch(() => setPending({}));
  }, [caseId, key]);
  return (
    <Card withBorder>
      <Group justify="space-between" mb="xs">
        <Title order={5}>Análises concluídas ({completed.length})</Title>
        {running > 0 && (
          <Text size="xs" c="dimmed">
            {running} análise(s) a decorrer — aparecem aqui quando terminarem.{" "}
            <Anchor size="xs" component="button" onClick={() => onOpenTab("jobs")}>Ver progresso</Anchor>
          </Text>
        )}
      </Group>
      {completed.length === 0 ? (
        <Text size="sm" c="dimmed">Ainda não há análises concluídas neste processo.</Text>
      ) : (
        <Table.ScrollContainer minWidth={760}>
          <Table highlightOnHover verticalSpacing={6}>
            <Table.Thead>
              <Table.Tr><Table.Th>#</Table.Th><Table.Th>Procura</Table.Th><Table.Th>Locais</Table.Th>
                <Table.Th>Resultado</Table.Th><Table.Th>Por rever</Table.Th><Table.Th>Concluída</Table.Th><Table.Th /></Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {completed.map((j) => {
                const discover = j.params.mode === "discover";
                const who = discover ? "Descoberta (todas as faces)"
                  : (j.params.persons ?? []).map((p) => p.label).join(", ") || "Procurar";
                const n = pending[j.id] ?? 0;
                return (
                  <Table.Tr key={j.id}>
                    <Table.Td>{j.id}</Table.Td>
                    <Table.Td>
                      <Text size="sm" maw={260} truncate title={who}>{who}</Text>
                      {j.params.clothing && <Badge size="xs" color="grape" variant="light">vestuário</Badge>}
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm" maw={220} truncate>{jobLocations(j, locations) || "—"}</Text>
                      <Text size="xs" c="dimmed">{j.video_ids.length} vídeo{j.video_ids.length === 1 ? "" : "s"}</Text>
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm">{discover ? `${j.persons} pessoa(s) distinta(s)` : `${j.matches} correspondência(s)`}</Text>
                      <Text size="xs" c="dimmed">{j.tracks} track(s)</Text>
                    </Table.Td>
                    <Table.Td>
                      {n ? <Badge color="gray" variant="light">{n} por rever</Badge>
                        : <Text size="xs" c="dimmed">—</Text>}
                    </Table.Td>
                    <Table.Td>
                      <Text size="xs">{j.finished_at ? formatDate(j.finished_at) : "—"}</Text>
                      <Text size="xs" c="dimmed">{j.created_by}</Text>
                    </Table.Td>
                    <Table.Td>
                      <Group gap="xs" wrap="nowrap" justify="flex-end">
                        <Button size="xs" rightSection={<IconExternalLink size={14} />}
                          onClick={() => navigate(`/jobs/${j.id}`)}>Abrir resultados</Button>
                        <Anchor size="xs" component="button" onClick={() => onShowImages(j.id)}>Ver imagens</Anchor>
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                );
              })}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}
    </Card>
  );
}

/** Separador Resultados: análises concluídas e imagens validadas (deteções confirmadas e frames guardados). */
export default function ValidatedFramesPanel({ caseId, jobs, locations: caseLocations, onOpenTab }: {
  caseId: number; jobs: Job[]; locations: Location[]; onOpenTab: (tab: string) => void;
}) {
  const navigate = useNavigate();
  const imagesRef = useRef<HTMLDivElement>(null);
  const [items, setItems] = useState<ValidatedItem[] | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [loc, setLoc] = useState("all");
  const [person, setPerson] = useState<string | null>(null);
  const [job, setJob] = useState<string | null>(null);
  const [kind, setKind] = useState("all");
  const [viewing, setViewing] = useState<number | null>(null);

  const load = useCallback(() => {
    api.validatedItems(caseId).then((xs) => { setItems(xs); setSelected(xs.map(keyOf)); }).catch(notifyError);
  }, [caseId]);
  useEffect(load, [load]);
  const doneKey = jobs.filter((j) => j.status === "completed").map((j) => j.id).join(",");
  useEffect(() => {
    if (items) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doneKey]);
  const showImages = (jobId: number) => {
    setJob(String(jobId)); setLoc("all"); setPerson(null); setKind("all");
    imagesRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  const header = (
    <CompletedJobs caseId={caseId} jobs={jobs} locations={caseLocations} onShowImages={showImages}
      onOpenTab={onOpenTab} />
  );

  const locations = useMemo(() => [...new Map((items ?? []).filter((i) => i.location_id != null)
    .map((i) => [i.location_id!, i.location_name])).entries()], [items]);
  const persons = useMemo(() => [...new Set((items ?? []).map((i) => i.person_label).filter(Boolean))].sort(), [items]);
  const itemJobs = useMemo(() => [...new Set([...(items ?? []).map((i) => i.job_id), ...(job ? [Number(job)] : [])])]
    .sort((a, b) => a - b), [items, job]);
  const hasSaved = (items ?? []).some((i) => i.type === "saved_frame");
  const filtered = (items ?? []).filter((i) => (loc === "all" || String(i.location_id) === loc)
    && (!person || i.person_label === person) && (!job || String(i.job_id) === job)
    && (kind === "all" || i.type === kind));
  // Com mais do que um local, as imagens aparecem divididas por local (pela ordem dos locais do processo)
  const order = new Map(caseLocations.map((l, n) => [l.id, n]));
  const groups = useMemo(() => {
    const byLoc = new Map<number | null, ValidatedItem[]>();
    for (const i of filtered) byLoc.set(i.location_id, [...(byLoc.get(i.location_id) ?? []), i]);
    return [...byLoc.entries()]
      .sort(([a], [b]) => (a == null ? 1e9 : order.get(a) ?? 1e8) - (b == null ? 1e9 : order.get(b) ?? 1e8))
      .map(([id, list]) => ({ id, name: list[0].location_name || "Sem local", items: list }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items, loc, person, job, kind, caseLocations]);
  const grouped = groups.length > 1;
  const visible = grouped ? groups.flatMap((g) => g.items) : filtered;
  const selectedSet = new Set(selected);
  const chosen = visible.filter((i) => selectedSet.has(keyOf(i)));

  const toggle = (k: string) => setSelected((s) => (s.includes(k) ? s.filter((x) => x !== k) : [...s, k]));
  const setVisible = (on: boolean) => {
    const keys = new Set(visible.map(keyOf));
    setSelected((s) => (on ? [...new Set([...s, ...keys])] : s.filter((x) => !keys.has(x))));
  };
  const download = () => {
    notifyOk(`A preparar o ZIP com ${chosen.length} imagem(ns) validada(s)…`, "Download");
    window.location.href = api.validatedFramesUrl("cases", caseId, {
      detections: chosen.filter((i) => i.type === "detection").map((i) => i.detection_id!),
      frames: chosen.filter((i) => i.type === "saved_frame").map((i) => i.saved_frame_id!),
    });
  };
  const removeSaved = async (i: ValidatedItem) => {
    try {
      await api.deleteSavedFrame(i.saved_frame_id!);
      notifyOk("Frame guardado retirado. O registo fica na auditoria.");
      load();
    } catch (e) {
      notifyError(e, "Não foi possível retirar o frame");
    }
  };

  if (!items) return <Stack>{header}<Loader size="sm" /></Stack>;
  if (!items.length) {
    return (
      <Stack>
        {header}
        <Title order={5}>Imagens validadas</Title>
        <Alert color="blue" icon={<IconInfoCircle size={18} />}>
          Ainda não há imagens validadas neste processo. Confirme as deteções (✓) nos resultados das análises, ou use
          "Guardar frame" no leitor da análise: aparecem aqui para ver e descarregar os frames originais.
        </Alert>
      </Stack>
    );
  }
  return (
    <Stack>
      {header}
      <div ref={imagesRef}><Title order={5}>Imagens validadas ({items.length})</Title></div>
      <Group justify="space-between" align="flex-end" wrap="wrap">
        <Group align="flex-end" wrap="wrap">
          {locations.length > 1 && (
            <Chip.Group multiple={false} value={loc} onChange={(v) => setLoc(v as string)}>
              <Group gap={4}>
                <Chip size="xs" value="all">Todos os locais</Chip>
                {locations.map(([id, name]) => <Chip key={id} size="xs" value={String(id)} color="indigo">{name}</Chip>)}
              </Group>
            </Chip.Group>
          )}
          {hasSaved && (
            <SegmentedControl size="xs" value={kind} onChange={setKind}
              data={[{ value: "all", label: "Todas" }, { value: "detection", label: "Deteções confirmadas" },
                { value: "saved_frame", label: "Frames guardados" }]} />
          )}
          {persons.length > 1 && (
            <Select size="xs" w={200} label="Pessoa" placeholder="Todas" clearable value={person} onChange={setPerson}
              data={persons} />
          )}
          {(itemJobs.length > 1 || job) && (
            <Select size="xs" w={150} label="Análise" placeholder="Todas" clearable value={job} onChange={setJob}
              data={itemJobs.map((j) => ({ value: String(j), label: `#${j}` }))} />
          )}
        </Group>
        <Group gap="xs">
          <Button size="xs" variant="default" onClick={() => setVisible(true)}>Selecionar todos</Button>
          <Button size="xs" variant="default" onClick={() => setVisible(false)}>Limpar seleção</Button>
          <Button size="xs" leftSection={<IconDownload size={14} />} disabled={!chosen.length} onClick={download}>
            Descarregar selecionados ({chosen.length})
          </Button>
        </Group>
      </Group>
      <Text size="sm" c="dimmed">
        {visible.length} imagem(ns) validada(s){visible.length !== items.length ? ` de ${items.length}` : ""} ·
        {" "}{chosen.length} selecionada(s). Clique numa imagem para a abrir. O ZIP leva o frame original (PNG, sem
        marcações) de cada imagem selecionada e o manifesto com SHA-256; o download fica na auditoria.
      </Text>
      {(grouped ? groups : [{ id: null, name: "", items: visible }]).map((g) => {
        const offset = visible.indexOf(g.items[0]);
        return (
          <Stack key={String(g.id)} gap="xs" data-testid={grouped ? `validated-group-${g.id ?? "none"}` : undefined}>
            {grouped && (
              <Group gap="xs" mt="sm">
                <Title order={6}>{g.name}</Title>
                <Badge size="sm" variant="light">{g.items.length} imagem(ns)</Badge>
              </Group>
            )}
            <SimpleGrid cols={{ base: 1, xs: 2, md: 3, xl: 4 }}>
              {g.items.map((i, n) => {
                const idx = offset + n;
          const k = keyOf(i);
          const on = selectedSet.has(k);
          const saved = i.type === "saved_frame";
          return (
            <Card key={k} withBorder padding="xs" data-testid={`validated-${k}`}
              style={{ outline: on ? "2px solid var(--mantine-color-blue-6)" : undefined }}>
              <Card.Section style={{ position: "relative", cursor: "zoom-in" }} onClick={() => setViewing(idx)}>
                <Image loading="lazy" decoding="async" src={thumbUrl(i)} h={170} fit="contain" bg="black" alt={`Frame de ${title(i)}`} />
                <Box style={{ position: "absolute", top: 8, left: 8 }} onClick={(e) => e.stopPropagation()}>
                  <Checkbox checked={on} onChange={() => toggle(k)} aria-label={`Selecionar ${k}`} />
                </Box>
                <Tooltip label="Abrir a foto">
                  <ActionIcon variant="filled" color="dark" size="sm" aria-label={`Abrir ${k}`}
                    style={{ position: "absolute", top: 8, right: 8, opacity: 0.8 }}>
                    <IconMaximize size={14} />
                  </ActionIcon>
                </Tooltip>
              </Card.Section>
              <Stack gap={2} mt={6}>
                <Group justify="space-between" wrap="nowrap" gap={4}>
                  <Text size="sm" fw={600} truncate title={i.video_filename}>{title(i)}</Text>
                  {saved ? <Badge size="sm" color="teal" variant="light">Frame guardado</Badge>
                    : i.person_label && <Badge size="sm" variant="light">{i.person_label}</Badge>}
                </Group>
                <Text size="xs" ff="monospace">{when(i)}</Text>
                <Text size="xs" c="dimmed">
                  Análise #{i.job_id} · {saved ? `frame ${i.frame_index}` : `deteção #${i.detection_id} · frame ${i.frame_index}`}
                  {!saved && (i.kind === "body" ? " · vestuário" : i.similarity != null ? ` · ${(i.similarity * 100).toFixed(0)}%` : "")}
                </Text>
                <Text size="xs" c="dimmed" truncate>
                  {saved ? "Guardado" : "Confirmada"} por {i.reviewed_by || "—"}{i.review_note ? ` · ${i.review_note}` : ""}
                </Text>
                <Group gap="xs" mt={4}>
                  <Anchor size="xs" href={downloadUrl(i)}>
                    <Group gap={2} wrap="nowrap"><IconPhotoDown size={12} />PNG original</Group>
                  </Anchor>
                  <Anchor size="xs" component="button"
                    onClick={() => navigate(`/jobs/${i.job_id}?video=${i.video_id}&t=${i.timestamp_s}`)}>
                    <Group gap={2} wrap="nowrap"><IconExternalLink size={12} />Abrir na análise</Group>
                  </Anchor>
                  {saved && (
                    <Anchor size="xs" component="button" c="red" onClick={() => removeSaved(i)}>
                      <Group gap={2} wrap="nowrap"><IconTrash size={12} />Retirar</Group>
                    </Anchor>
                  )}
                </Group>
              </Stack>
            </Card>
          );
        })}
            </SimpleGrid>
          </Stack>
        );
      })}
      {!visible.length && <Text size="sm" c="dimmed">Sem imagens validadas com estes filtros.</Text>}
      <PhotoViewer items={visible} index={viewing} onIndex={setViewing} onClose={() => setViewing(null)} />
    </Stack>
  );
}
