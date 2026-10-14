import { Badge, Button, Card, Group, Loader, Stack, Table, Text, Tooltip } from "@mantine/core";
import { IconClock, IconMovie, IconRefresh } from "@tabler/icons-react";
import { useEffect, useState } from "react";

import { api, formatBytes, formatTime, realTime } from "../api";
import { notifyError, notifyOk } from "../notify";
import type { Video } from "../types";
import ClockModal from "./ClockModal";
import PlaybackBadge from "./PlaybackBadge";
import UploadZone from "./UploadZone";

const VIDEO_ACCEPT = {
  "video/*": [".mp4", ".avi", ".mkv", ".mov", ".m4v", ".wmv", ".mpg", ".mpeg", ".ts", ".webm", ".flv", ".3gp"],
  "application/octet-stream": [".dav", ".h264", ".264"],
};

interface Props {
  caseId: number;
  videos: Video[];
  onChange: () => void;
}

export default function VideosPanel({ caseId, videos, onChange }: Props) {
  const [progress, setProgress] = useState<number | null>(null);
  const [clockVideo, setClockVideo] = useState<Video | null>(null);

  // Atualiza o estado das conversões para o browser enquanto houver alguma na fila ou a decorrer.
  // (e enquanto a hora estiver a ser lida por OCR na importação).
  const converting = videos.some((v) => v.playback_status === "pending" || v.playback_status === "running"
    || v.clock_auto_status === "pending" || v.clock_auto_status === "running");
  useEffect(() => {
    if (!converting) return;
    const t = setInterval(onChange, 3000);
    return () => clearInterval(t);
  }, [converting, onChange]);

  const convertAll = async () => {
    try {
      const vs = await api.createCaseCopies(caseId);
      const queued = vs.filter((v) => v.playback_status === "pending" || v.playback_status === "running").length;
      notifyOk(queued ? `${queued} vídeo(s) na fila de conversão para o browser.` : "Todos os vídeos já têm cópia de reprodução.");
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
      const created = await api.uploadVideos(caseId, files, setProgress);
      notifyOk(`${created.length} vídeo(s) importado(s).`);
    } catch (e) {
      notifyError(e, "Falha ao importar vídeo");
    } finally {
      setProgress(null);
      onChange();
    }
  };

  return (
    <Stack>
      <UploadZone accept={VIDEO_ACCEPT} onDrop={upload} progress={progress}
        icon={<IconMovie size={40} stroke={1.5} />}
        title="Arraste vídeos de CCTV"
        hint="MP4, AVI, MKV, MOV, … Envio em blocos: se a ligação falhar, volte a largar o mesmo ficheiro para continuar de onde parou." />
      {videos.length > 0 && (
        <Group justify="flex-end">
          <Button size="xs" variant="light" leftSection={<IconRefresh size={14} />} onClick={convertAll}>
            Criar cópias de reprodução de todos os vídeos
          </Button>
        </Group>
      )}
      <Card withBorder p={0}>
        {videos.length === 0 ? (
          <Text c="dimmed" p="md">Sem vídeos importados.</Text>
        ) : (
          <Table.ScrollContainer minWidth={900}>
            <Table>
              <Table.Thead>
                <Table.Tr><Table.Th>#</Table.Th><Table.Th>Ficheiro</Table.Th><Table.Th>Tamanho</Table.Th>
                  <Table.Th>Duração</Table.Th><Table.Th>FPS</Table.Th><Table.Th>Resolução</Table.Th><Table.Th>Codec</Table.Th><Table.Th>Reprodução no browser</Table.Th><Table.Th>Hora real</Table.Th></Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {videos.map((v) => (
                  <Table.Tr key={v.id}>
                    <Table.Td>{v.id}</Table.Td><Table.Td>{v.filename}</Table.Td><Table.Td>{formatBytes(v.size_bytes)}</Table.Td>
                    <Table.Td>{formatTime(v.duration_s)}</Table.Td><Table.Td>{v.fps.toFixed(2)}</Table.Td>
                    <Table.Td>{v.width}×{v.height}</Table.Td><Table.Td>{v.codec || "—"}</Table.Td>
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
                      <Button size="compact-xs" variant="subtle" leftSection={<IconClock size={14} />} mt={4}
                        onClick={() => setClockVideo(v)}>Acertar hora</Button>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        )}
      </Card>
      {clockVideo && (
        <ClockModal key={clockVideo.id} video={clockVideo} opened onClose={() => setClockVideo(null)}
          onSaved={(v) => { setClockVideo(v); onChange(); }} />
      )}
    </Stack>
  );
}
