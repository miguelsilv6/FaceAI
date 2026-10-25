import { Alert, Badge, Button, Group, Modal, Stack, Text } from "@mantine/core";
import { IconAlertTriangle } from "@tabler/icons-react";
import { useRef, useState } from "react";

import { api, formatTime, realTime } from "../api";
import { notifyError } from "../notify";
import { cameraLabel, cameraName } from "../places";
import { VIDEO_KEYS_HINT, useHtmlVideoKeys } from "../useVideoKeys";
import { preparingCopy, useOnDemandCopies } from "../playbackOnDemand";
import type { Location, Video } from "../types";
import { aspectCorrected, displaySize, videoStyle } from "../videoAspect";

/** Leitor de um vídeo importado, com o tempo do vídeo e a hora real do DVR (se acertada). Só visualização. */
export default function VideoPlayerModal({ video: initial, onClose, onChange, locations = [] }: {
  video: Video | null; onClose: () => void; onChange: () => void; locations?: Location[];
}) {
  const [t, setT] = useState(0);
  const [error, setError] = useState(false);
  const ref = useRef<HTMLVideoElement>(null);
  const [video = null] = useOnDemandCopies(initial ? [initial] : []); // cópia leve pedida ao abrir, se precisa
  useHtmlVideoKeys(ref, !!video);
  if (!video) return null;
  const preparing = preparingCopy(video);
  const createCopy = async () => {
    try {
      await api.createPlaybackCopy(video.id);
      setError(false);
      onChange();
    } catch (e) {
      notifyError(e);
    }
  };
  return (
    <Modal opened onClose={onClose} size="xl" title={`#${video.id} ${cameraLabel(video, locations)}${cameraName(video) === video.filename ? "" : ` (${video.filename})`}`}>
      <Stack gap="xs">
        {preparing ? (
          <Alert color="blue">
            O browser não abre este formato: a preparar uma cópia só para visualização ({video.playback_status === "running"
              ? `${Math.round(video.playback_progress * 100)}%` : "em fila"}). A análise usa sempre o original.
          </Alert>
        ) : (
          <video ref={ref} key={`${video.id}-${video.playback_status}-${video.playback_format}`} src={api.videoUrl(video)}
            controls autoPlay preload="metadata" aria-label={`Leitor de ${video.filename}`}
            onTimeUpdate={(e) => setT(e.currentTarget.currentTime)} onError={() => setError(true)}
            onLoadedData={() => setError(false)}
            style={videoStyle(video, "70vh", { background: "#000", borderRadius: 6 })} />
        )}
        {error && !preparing && (
          <Alert color="orange" icon={<IconAlertTriangle size={18} />}>
            O browser não consegue reproduzir este vídeo.{" "}
            {video.playback_status === "unavailable" ? video.playback_error : (
              <Button size="compact-xs" variant="light" onClick={createCopy}>Criar cópia de reprodução</Button>
            )}
          </Alert>
        )}
        <Group justify="space-between" wrap="wrap">
          <Group gap="xs">
            <Text size="sm" ff="monospace">vídeo {formatTime(t)} / {formatTime(video.duration_s)}</Text>
            {!preparing && <Text size="xs" c="dimmed">{VIDEO_KEYS_HINT}</Text>}
          </Group>
          {video.dvr_start ? (
            <Group gap={6}>
              <Badge variant="light" color={video.time_source === "ocr" ? "teal" : "blue"}>hora real</Badge>
              <Text size="sm" ff="monospace" fw={600}>{realTime(video, t)}</Text>
            </Group>
          ) : <Text size="xs" c="dimmed">Hora real não acertada</Text>}
        </Group>
        <Text size="xs" c="dimmed">
          {video.width}×{video.height}{aspectCorrected(video) ? ` (mostrado como ${displaySize(video).join("×")})` : ""} · {video.fps.toFixed(2)} fps · {video.codec || "codec ?"}
          {video.playback_status === "ready" ? ` · a reproduzir a cópia de visualização ${video.playback_format === "webm" ? "VP9/WebM" : "H.264"} (a análise usa o original)` : ""}
        </Text>
      </Stack>
    </Modal>
  );
}
