import { Card, Table, Text, Stack } from "@mantine/core";
import { IconMovie } from "@tabler/icons-react";
import { useState } from "react";

import { api, formatBytes, formatTime } from "../api";
import { notifyError, notifyOk } from "../notify";
import type { Video } from "../types";
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
      <Card withBorder p={0}>
        {videos.length === 0 ? (
          <Text c="dimmed" p="md">Sem vídeos importados.</Text>
        ) : (
          <Table.ScrollContainer minWidth={700}>
            <Table>
              <Table.Thead>
                <Table.Tr><Table.Th>#</Table.Th><Table.Th>Ficheiro</Table.Th><Table.Th>Tamanho</Table.Th>
                  <Table.Th>Duração</Table.Th><Table.Th>FPS</Table.Th><Table.Th>Resolução</Table.Th><Table.Th>Codec</Table.Th></Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {videos.map((v) => (
                  <Table.Tr key={v.id}>
                    <Table.Td>{v.id}</Table.Td><Table.Td>{v.filename}</Table.Td><Table.Td>{formatBytes(v.size_bytes)}</Table.Td>
                    <Table.Td>{formatTime(v.duration_s)}</Table.Td><Table.Td>{v.fps.toFixed(2)}</Table.Td>
                    <Table.Td>{v.width}×{v.height}</Table.Td><Table.Td>{v.codec || "—"}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        )}
      </Card>
    </Stack>
  );
}
