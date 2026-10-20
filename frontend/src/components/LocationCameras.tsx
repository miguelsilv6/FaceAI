import {
  ActionIcon, Alert, Badge, Button, Card, Group, Loader, Menu, Stack, Switch, Table, Text, TextInput, Title, Tooltip,
} from "@mantine/core";
import {
  IconArrowsExchange, IconCheck, IconClock, IconDatabase, IconDots, IconMapPin, IconMovie, IconPencil, IconPlayerPlay, IconTrash, IconX,
} from "@tabler/icons-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, formatBytes, formatDate, formatTime, realTime } from "../api";
import { notifyError, notifyOk } from "../notify";
import { cameraName, locationAnalysis, type LocationAnalysis, videoAnalyses } from "../places";
import AnalysisBadge from "./AnalysisBadge";
import type { Job, Location, Video } from "../types";
import { usePolling } from "../usePolling";
import ClockModal from "./ClockModal";
import DeleteVideoModal from "./DeleteVideoModal";
import FpsModal from "./FpsModal";
import PlaybackBadge from "./PlaybackBadge";
import UploadZone from "./UploadZone";
import VideoPlayerModal from "./VideoPlayerModal";
import { confirmAction } from "../confirm";

/** Resumo da extração de um formato proprietário (.sec), para a dica do vídeo. */
function extractionTitle(v: Video): string {
  const x = v.extraction ?? {};
  const original = v.original_removed_at
    ? `Original ${v.filename} removido do servidor (SHA-256 ${v.sha256}).`
    : `Original ${v.filename} guardado intacto (SHA-256 ${v.sha256}).`;
  if (x.method?.startsWith("remux")) {
    return [original, `A análise usa uma cópia por remux sem recodificação (${x.container}), com os mesmos pacotes `
      + `de vídeo do original (${x.packets} pacotes).`, `Cópia de análise: SHA-256 ${v.analysis_sha256}`].join("\n");
  }
  return [
    original,
    `A análise usa o H.264 extraído dos blocos: ${x.frames} frames, ${x.keyframes} chave, ${x.fps} fps (${x.fps_source}).`,
    x.resyncs ? `${x.resyncs} ressincronização(ões), ${formatBytes(x.skipped_bytes ?? 0)} ignorados.` : "Sem blocos danificados.",
    ...(x.fps_history ?? []).map((h) => `fps corrigido de ${h.from} para ${h.to} (${h.source}; ${h.reason}; ${h.by}).`),
    `Cópia de análise (${x.container}): SHA-256 ${x.analysis_sha256}`,
  ].join("\n");
}

export const VIDEO_ACCEPT = {
  "video/*": [".mp4", ".avi", ".mkv", ".mov", ".m4v", ".wmv", ".mpg", ".mpeg", ".ts", ".webm", ".flv", ".3gp"],
  "application/octet-stream": [".dav", ".h264", ".264", ".sec"],
};

interface Props {
  caseId: number;
  location: Location;
  videos: Video[]; // todos os vídeos do caso (os deste local são filtrados aqui)
  locations: Location[];
  jobs: Job[];
  canManage: boolean;
  onChange: () => void;
  onDeleted: () => void; // o local foi eliminado
}

/** Nome da câmara editável no próprio quadro (vazio = nome do ficheiro). Guarda ao sair do campo ou com Enter. */
function CameraNameInput({ video, onSaved }: { video: Video; onSaved: () => void }) {
  const [value, setValue] = useState(video.camera_name);
  useEffect(() => setValue(video.camera_name), [video.camera_name]);
  const save = async () => {
    const name = value.trim();
    if (name === video.camera_name) return;
    try {
      await api.updateVideo(video.id, { camera_name: name });
      onSaved();
    } catch (e) {
      notifyError(e, "Não foi possível mudar o nome do vídeo");
      setValue(video.camera_name);
    }
  };
  return (
    <TextInput size="xs" variant="filled" value={value} placeholder={video.filename} miw={160}
      aria-label={`Nome do vídeo #${video.id}`}
      onChange={(e) => setValue(e.currentTarget.value)} onBlur={save}
      onKeyDown={(e) => {
        if (e.key === "Enter") e.currentTarget.blur();
        if (e.key === "Escape") setValue(video.camera_name);
      }} />
  );
}

/** Cabeçalho de um local: nome (editável), n.º de câmaras e eliminação (só vazio). */
function LocationHeader({ location, cameras, duration, analysis, canManage, onChange, onDeleted }: {
  location: Location; cameras: number; duration: number; analysis: LocationAnalysis; canManage: boolean;
  onChange: () => void; onDeleted: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(location.name);
  const rename = async () => {
    try {
      await api.renameLocation(location.id, name.trim());
      setEditing(false);
      onChange();
    } catch (e) {
      notifyError(e, "Não foi possível mudar o nome do local");
    }
  };
  const remove = async () => {
    if (!await confirmAction({ title: `Eliminar o local "${location.name}"?`, message: "O local está vazio. Fica registado na auditoria.",
      confirmLabel: "Eliminar", danger: true })) return;
    try {
      await api.deleteLocation(location.id);
      notifyOk(`Local "${location.name}" eliminado.`);
      onDeleted();
    } catch (e) {
      notifyError(e, "Não foi possível eliminar o local");
    }
  };
  return (
    <Group justify="space-between" wrap="nowrap">
      <Group gap="xs" wrap="nowrap">
        <IconMapPin size={20} color="var(--mantine-color-indigo-6)" />
        {editing ? (
          <Group gap={4} wrap="nowrap">
            <TextInput size="xs" value={name} data-autofocus aria-label="Nome do local"
              onChange={(e) => setName(e.currentTarget.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && name.trim()) rename(); if (e.key === "Escape") setEditing(false); }} />
            <ActionIcon size="sm" variant="light" color="green" aria-label="Guardar nome do local"
              disabled={!name.trim()} onClick={rename}><IconCheck size={14} /></ActionIcon>
            <ActionIcon size="sm" variant="subtle" color="gray" aria-label="Cancelar"
              onClick={() => { setEditing(false); setName(location.name); }}><IconX size={14} /></ActionIcon>
          </Group>
        ) : (
          <>
            <Title order={3}>{location.name}</Title>
            <Tooltip label="Mudar o nome do local">
              <ActionIcon size="sm" variant="subtle" aria-label={`Mudar o nome de ${location.name}`}
                onClick={() => { setName(location.name); setEditing(true); }}><IconPencil size={14} /></ActionIcon>
            </Tooltip>
          </>
        )}
        <Badge variant="light">{cameras} vídeo{cameras === 1 ? "" : "s"}</Badge>
        {cameras > 0 && <Text size="xs" c="dimmed">{formatTime(duration)} de vídeo</Text>}
        <AnalysisBadge analysis={analysis} size="sm" />
        {cameras > 0 && (
          <Tooltip label={`Originais ${formatBytes(location.originals_bytes)} · cópias de análise e reprodução ${formatBytes(location.playback_bytes)}`}>
            <Badge variant="default" leftSection={<IconDatabase size={12} />}>{formatBytes(location.size_bytes)} em disco</Badge>
          </Tooltip>
        )}
      </Group>
      {canManage && (
        <Tooltip label={cameras ? "Mova ou elimine os vídeos para poder eliminar o local" : "Eliminar o local"}>
          <span>
            <ActionIcon variant="subtle" color="red" disabled={cameras > 0} onClick={remove}
              aria-label={`Eliminar o local ${location.name}`}><IconTrash size={16} /></ActionIcon>
          </span>
        </Tooltip>
      )}
    </Group>
  );
}

/** Página de um local: importação dos vídeos das câmaras desse local e a tabela das câmaras. */
export default function LocationCameras({ caseId, location, videos, locations, jobs, canManage, onChange, onDeleted }: Props) {
  const [tech, setTech] = useState(() => {
    try {
      return localStorage.getItem("faceai.videoTech") === "1";
    } catch {
      return false;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem("faceai.videoTech", tech ? "1" : "0");
    } catch { /* sem armazenamento */ }
  }, [tech]);
  const [progress, setProgress] = useState<number | null>(null);
  const [clockVideo, setClockVideo] = useState<Video | null>(null);
  const [playId, setPlayId] = useState<number | null>(null);
  const [deleteId, setDeleteId] = useState<number | null>(null);
  const [fpsVideo, setFpsVideo] = useState<Video | null>(null);
  const [duplicates, setDuplicates] = useState<{ file: string; message: string }[]>([]);
  const navigate = useNavigate();
  const vs = videos.filter((v) => v.location_id === location.id);

  // Atualiza o estado das conversões para o browser (e da leitura da hora por OCR) enquanto decorrem.
  const converting = vs.some((v) => v.playback_status === "pending" || v.playback_status === "running"
    || v.clock_auto_status === "pending" || v.clock_auto_status === "running"
    || v.extraction_status === "pending" || v.extraction_status === "running");
  usePolling(onChange, 3000, converting);

  const retryExtraction = async (v: Video) => {
    try {
      await api.retryExtraction(v.id);
      notifyOk(`Extração do vídeo #${v.id} na fila.`);
      onChange();
    } catch (e) {
      notifyError(e);
    }
  };

  const createCopy = async (v: Video) => {
    try {
      await api.createPlaybackCopy(v.id);
      notifyOk(`Cópia de reprodução do vídeo #${v.id} na fila.`);
      onChange();
    } catch (e) {
      notifyError(e);
    }
  };

  const upload = async (files: File[]) => {
    if (!files.length) return;
    setProgress(0);
    try {
      const { created, duplicates: dups } = await api.uploadVideos(caseId, files, setProgress, location.id);
      setDuplicates(dups);
      if (created.length) notifyOk(`${created.length} vídeo(s) importado(s) para "${location.name}".`
        + (created.some((v) => v.extraction_status) ? " Os .sec estão a ser extraídos (pode demorar alguns minutos)." : ""));
    } catch (e) {
      notifyError(e, "Falha ao importar vídeo");
    } finally {
      setProgress(null);
      onChange();
    }
  };

  const move = async (v: Video, to: Location) => {
    try {
      await api.updateVideo(v.id, { location_id: to.id });
      notifyOk(`"${cameraName(v)}" movido para "${to.name}".`);
      onChange();
    } catch (e) {
      notifyError(e, "Não foi possível mover o vídeo");
    }
  };

  return (
    <Stack>
      <LocationHeader location={location} cameras={vs.length} duration={vs.reduce((n, v) => n + v.duration_s, 0)}
        analysis={locationAnalysis(location.id, videos, jobs)} canManage={canManage} onChange={onChange}
        onDeleted={onDeleted} />
      <UploadZone accept={VIDEO_ACCEPT} onDrop={upload} progress={progress}
        icon={<IconMovie size={40} stroke={1.5} />}
        title={`Importar vídeos de CCTV para "${location.name}"`}
        hint="Um ficheiro por ângulo de CCTV. Envio em blocos: se a ligação falhar, volte a largar o mesmo ficheiro para continuar de onde parou." />
      {duplicates.length > 0 && (
        <Alert color="yellow" title={`${duplicates.length} ficheiro(s) não importado(s): já existem neste processo ou repetem-se no lote`}
          withCloseButton closeButtonLabel="Fechar" onClose={() => setDuplicates([])}>
          <Stack gap={2}>
            {duplicates.map((d) => <Text key={d.file} size="sm">{d.message}</Text>)}
            <Text size="xs" c="dimmed">A comparação é feita pelo conteúdo (SHA-256), não pelo nome do ficheiro.</Text>
          </Stack>
        </Alert>
      )}
      <Card withBorder p={0}>
        {vs.length > 0 && (
          <Group justify="flex-end" px="sm" pt="xs">
            <Switch size="xs" label="Detalhes técnicos" checked={tech} onChange={(e) => setTech(e.currentTarget.checked)} />
          </Group>
        )}
        {vs.length === 0 ? (
          <Text c="dimmed" p="md">Sem vídeos neste local. Importe-os acima.</Text>
        ) : (
          <Table.ScrollContainer minWidth={tech ? 1000 : 760}>
            <Table>
              <Table.Thead>
                <Table.Tr>{tech && <Table.Th>#</Table.Th>}<Table.Th>Vídeo</Table.Th>{tech && <Table.Th>Tamanho</Table.Th>}
                  <Table.Th>Duração</Table.Th>{tech && <><Table.Th>FPS</Table.Th><Table.Th>Resolução</Table.Th></>}
                  <Table.Th>{tech ? "Codec" : "Estado"}</Table.Th><Table.Th>Reprodução no browser</Table.Th><Table.Th>Hora real</Table.Th><Table.Th>Análises</Table.Th><Table.Th /></Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {vs.map((v) => (
                  <Table.Tr key={v.id}>
                    {tech && <Table.Td>{v.id}</Table.Td>}
                    <Table.Td>
                      <Group gap={6} wrap="nowrap">
                        <Tooltip label="Ver o vídeo">
                          <ActionIcon variant="light" size="sm" aria-label={`Ver ${v.filename}`} onClick={() => setPlayId(v.id)}>
                            <IconPlayerPlay size={14} />
                          </ActionIcon>
                        </Tooltip>
                        <Stack gap={0}>
                          <CameraNameInput video={v} onSaved={onChange} />
                          <Text size="xs" c="dimmed" truncate maw={260}
                            title={v.sha256 ? `${v.filename}\nSHA-256 ${v.sha256}` : v.filename}>
                            {v.filename}{tech && v.sha256 ? ` · ${v.sha256.slice(0, 12)}…` : ""}
                          </Text>
                        </Stack>
                      </Group>
                    </Table.Td>{tech && <Table.Td>{formatBytes(v.size_bytes)}</Table.Td>}
                    <Table.Td>{formatTime(v.duration_s)}</Table.Td>
                    {tech && <><Table.Td>{v.fps.toFixed(2)}</Table.Td><Table.Td>{v.width}×{v.height}</Table.Td></>}
                    <Table.Td>
                      {tech ? v.codec || "—" : null}
                      {(v.extraction_status === "pending" || v.extraction_status === "running") && (
                        <Group gap={4} wrap="nowrap">
                          <Loader size={10} />
                          <Text size="xs" c="dimmed">
                            {v.filename.toLowerCase().endsWith(".sec")
                              ? (v.extraction_status === "pending" ? "na fila para extrair"
                                : `a extrair… ${Math.round((v.extraction_progress ?? 0) * 100)}%`)
                              : "a preparar cópia de análise sem perdas…"}
                          </Text>
                        </Group>
                      )}
                      {v.extraction_status === "failed" && (
                        <Stack gap={2}>
                          <Text size="xs" c="red" maw={260}>extração falhou: {v.extraction_error}</Text>
                          {canManage && (
                            <Button size="compact-xs" variant="subtle" onClick={() => retryExtraction(v)}>
                              tentar de novo
                            </Button>
                          )}
                        </Stack>
                      )}
                      {v.original_removed_at && (
                        <Tooltip multiline w={320} label={`Original apagado do servidor em ${new Date(v.original_removed_at)
                          .toLocaleString()} depois de verificada a cópia de análise sem perdas (${formatBytes(v.size_bytes)}
                          libertados). SHA-256 do original: ${v.sha256}. Reenvie o mesmo ficheiro para o voltar a anexar.`}>
                          <Badge size="xs" variant="light" color="gray">original removido</Badge>
                        </Tooltip>
                      )}
                      {!v.original_removed_at && v.extraction?.original_kept_reason && (
                        <Tooltip multiline w={320} label={`O original não foi removido porque a verificação da cópia falhou:
                          ${v.extraction.original_kept_reason}. A análise usa a cópia; o original fica no servidor.`}>
                          <Badge size="xs" variant="light" color="orange">original mantido</Badge>
                        </Tooltip>
                      )}
                      {(v.extraction_status === "ready" || !v.extraction_status) && v.extraction_error && (
                        <Text size="xs" c="orange" maw={260}>{v.extraction_error}</Text>
                      )}
                      {v.extraction?.method && (
                        <Text size="xs" c="dimmed" title={extractionTitle(v)}>
                          {v.extraction.method.startsWith("remux") ? "cópia por remux (sem recodificar)"
                            : "extraído (sem recodificar)"}
                        </Text>
                      )}
                      {v.extraction?.method && !v.extraction.method.startsWith("remux")
                        && (v.extraction.fps_assumed || v.extraction.suggested_fps) && (
                        <Text size="xs" c="orange" title="O fps pode estar errado: a reprodução e os tempos ficam acelerados ou lentos.">
                          {v.extraction.suggested_fps ? `relógio do DVR: ≈${v.extraction.suggested_fps.fps} fps`
                            : "fps assumido"}
                        </Text>
                      )}
                      {v.extraction?.method && !v.extraction.method.startsWith("remux") && canManage
                        && v.extraction_status === "ready" && (
                        <Button size="compact-xs" variant="subtle" onClick={() => setFpsVideo(v)}>corrigir fps…</Button>
                      )}
                    </Table.Td>
                    <Table.Td><PlaybackBadge video={v} onCreate={createCopy} /></Table.Td>
                    <Table.Td>
                      {v.dvr_start ? (
                        <Stack gap={2}>
                          <Text size="xs" ff="monospace">{realTime(v, 0)} → {realTime(v, v.duration_s)}</Text>
                          <Tooltip label={v.time_note || (v.time_source === "ocr" ? "Lida por OCR" : "Acerto manual")}
                            multiline w={260}>
                            <Badge size="xs" variant="light" color={v.time_source === "ocr" ? "teal" : "blue"}>
                              {v.time_source === "ocr" ? (v.clock_auto_status === "done" ? "OCR automático" : "OCR") : "manual"}
                              {v.clock_offset_s ? ` · desvio ${v.clock_offset_s > 0 ? "+" : ""}${v.clock_offset_s} s` : ""}
                            </Badge>
                          </Tooltip>
                        </Stack>
                      ) : v.clock_auto_status === "pending" || v.clock_auto_status === "running" ? (
                        <Group gap={6} wrap="nowrap">
                          <Loader size={12} />
                          <Text size="xs" c="dimmed">{v.clock_auto_status === "running" ? "A ler a hora (OCR)…" : "Leitura da hora em fila…"}</Text>
                        </Group>
                      ) : v.clock_auto_status === "failed" || v.clock_auto_status === "unavailable" ? (
                        <Tooltip multiline w={320} label={`${v.clock_auto_message || "Sem leitura."} Acerte a hora manualmente ou marque a zona da data/hora.`}>
                          <Badge size="xs" color="orange" variant="light">
                            {v.clock_auto_status === "unavailable" ? "OCR indisponível" : "OCR sem resultado"}
                          </Badge>
                        </Tooltip>
                      ) : <Text size="xs" c="dimmed">não acertada</Text>}
                      {canManage && (
                        <Button size="compact-xs" variant="subtle" leftSection={<IconClock size={14} />} mt={4}
                          onClick={() => setClockVideo(v)}>Acertar hora</Button>
                      )}
                    </Table.Td>
                    <Table.Td>
                      <Group gap={4}>
                        {videoAnalyses(v.id, jobs).map((j) => (
                          <Tooltip key={j.id} label={`Abrir os resultados da análise #${j.id} (${formatDate(j.finished_at)})`}>
                            <Badge size="xs" variant="light" color="teal" style={{ cursor: "pointer" }}
                              onClick={() => navigate(`/jobs/${j.id}?video=${v.id}`)}>#{j.id}</Badge>
                          </Tooltip>
                        ))}
                        {!videoAnalyses(v.id, jobs).length && <Text size="xs" c="dimmed">por analisar</Text>}
                      </Group>
                    </Table.Td>
                    <Table.Td w={40}>
                      <Menu position="bottom-end" withinPortal>
                        <Menu.Target>
                          <ActionIcon variant="subtle" aria-label={`Ações do vídeo ${v.filename}`}><IconDots size={16} /></ActionIcon>
                        </Menu.Target>
                        <Menu.Dropdown>
                          <Menu.Label>Mover para outro local</Menu.Label>
                          {locations.filter((l) => l.id !== v.location_id).map((l) => (
                            <Menu.Item key={l.id} leftSection={<IconArrowsExchange size={14} />} onClick={() => move(v, l)}>
                              {l.name}
                            </Menu.Item>
                          ))}
                          {locations.length < 2 && <Menu.Item disabled>Crie outro local primeiro</Menu.Item>}
                          {canManage && <>
                            <Menu.Divider />
                            <Menu.Item color="red" leftSection={<IconTrash size={14} />} onClick={() => setDeleteId(v.id)}>
                              Eliminar vídeo
                            </Menu.Item>
                          </>}
                        </Menu.Dropdown>
                      </Menu>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        )}
      </Card>
      <VideoPlayerModal key={playId ?? 0} video={vs.find((v) => v.id === playId) ?? null} onClose={() => setPlayId(null)}
        onChange={onChange} locations={locations} />
      <DeleteVideoModal video={vs.find((v) => v.id === deleteId) ?? null} jobs={jobs} locations={locations}
        onClose={() => setDeleteId(null)} onDeleted={() => { setDeleteId(null); onChange(); }} />
      {fpsVideo && (
        <FpsModal key={fpsVideo.id} video={fpsVideo} jobs={jobs} onClose={() => setFpsVideo(null)}
          onSaved={() => { setFpsVideo(null); onChange(); }} />
      )}
      {clockVideo && (
        <ClockModal key={clockVideo.id} video={clockVideo} opened onClose={() => setClockVideo(null)} locations={locations}
          onSaved={(v) => { setClockVideo(v); onChange(); }} />
      )}
    </Stack>
  );
}
