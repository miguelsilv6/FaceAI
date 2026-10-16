import { Alert, Button, Group, Modal, Stack, Text, TextInput, Textarea } from "@mantine/core";
import { IconAlertTriangle, IconTrash } from "@tabler/icons-react";
import { useEffect, useState } from "react";

import { api, formatBytes } from "../api";
import { notifyError, notifyOk } from "../notify";
import { cameraLabel } from "../places";
import type { Job, Location, Video } from "../types";

/** Confirmação da eliminação de um vídeo: bloqueada se houver análises que o usem; motivo vai para a auditoria. */
export default function DeleteVideoModal({ video, jobs, onClose, onDeleted, locations = [] }: {
  video: Video | null; jobs: Job[]; onClose: () => void; onDeleted: () => void; locations?: Location[];
}) {
  const [typed, setTyped] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    setTyped("");
    setReason("");
  }, [video?.id]);
  if (!video) return null;
  const usedBy = jobs.filter((j) => j.video_ids.includes(video.id));
  const confirmed = typed.trim() === String(video.id);
  const remove = async () => {
    setBusy(true);
    try {
      await api.deleteVideo(video.id, reason.trim());
      notifyOk(`O vídeo "${video.filename}" foi eliminado. O registo fica na auditoria do caso.`);
      onDeleted();
    } catch (e) {
      notifyError(e, "Não foi possível eliminar o vídeo");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal opened onClose={onClose} title={`Eliminar o vídeo #${video.id}?`} size="lg">
      <Stack>
        <Text size="sm"><b>{cameraLabel(video, locations)}</b> · {video.filename} · {formatBytes(video.size_bytes)}</Text>
        {usedBy.length ? (
          <Alert color="orange" icon={<IconAlertTriangle size={18} />}>
            Este vídeo é usado nas análises {usedBy.map((j) => `#${j.id}`).join(", ")}. Os resultados dependem do vídeo
            original: elimine primeiro essas análises (separador Análises).
          </Alert>
        ) : (
          <>
            <Alert color="red" icon={<IconAlertTriangle size={18} />}>
              Esta ação não pode ser desfeita. São apagados o ficheiro original e a cópia de reprodução. A auditoria
              mantém o histórico do vídeo e regista a eliminação (quem, quando, motivo e metadados).
            </Alert>
            <Textarea label="Motivo (fica registado na auditoria)" placeholder="Ex.: vídeo duplicado, local errado…"
              autosize minRows={2} value={reason} onChange={(e) => setReason(e.currentTarget.value)} />
            <TextInput label={`Para confirmar, escreva o número do vídeo (${video.id})`} value={typed}
              onChange={(e) => setTyped(e.currentTarget.value)} />
          </>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>Cancelar</Button>
          <Button color="red" leftSection={<IconTrash size={16} />} disabled={!confirmed || usedBy.length > 0}
            loading={busy} onClick={remove}>Eliminar vídeo</Button>
        </Group>
      </Stack>
    </Modal>
  );
}
