import { Alert, Button, Group, List, Modal, Stack, Text, TextInput, Textarea } from "@mantine/core";
import { IconAlertTriangle, IconTrash } from "@tabler/icons-react";
import { useEffect, useState } from "react";

import { api, formatDate } from "../api";
import { notifyError, notifyOk } from "../notify";
import type { Job } from "../types";

interface Props {
  job: Job | null;
  onClose: () => void;
  onDeleted: (job: Job) => void;
}

/** Confirmação da eliminação de uma análise: é preciso escrever o número da análise; o motivo vai para a auditoria. */
export default function DeleteJobModal({ job, onClose, onDeleted }: Props) {
  const [typed, setTyped] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    setTyped("");
    setReason("");
  }, [job?.id]);

  const confirmed = job != null && typed.trim() === String(job.id);
  const remove = async () => {
    if (!job || !confirmed) return;
    setBusy(true);
    try {
      await api.deleteJob(job.id, reason.trim());
      notifyOk(`A análise #${job.id} foi eliminada. O registo fica na auditoria do caso.`);
      onDeleted(job);
    } catch (e) {
      notifyError(e, "Não foi possível eliminar a análise");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal opened={job != null} onClose={onClose} title={job ? `Eliminar a análise #${job.id}?` : ""} size="lg">
      {job && (
        <Stack>
          <Alert color="red" icon={<IconAlertTriangle size={18} />}>
            Esta ação não pode ser desfeita. São apagados:
            <List size="sm" mt={4}>
              <List.Item>as deteções, os tracks{job.params.mode === "discover" ? ", as pessoas" : ""} e as revisões feitas;</List.Item>
              <List.Item>os recortes, os frames e os PNG originais gerados pela análise.</List.Item>
            </List>
            <Text size="sm" mt={4}>
              Os vídeos e as imagens de referência do caso <b>não</b> são apagados. A auditoria mantém o histórico da
              análise e regista a eliminação (quem, quando, motivo e parâmetros).
            </Text>
          </Alert>
          <Text size="sm" c="dimmed">
            Criada em {formatDate(job.created_at)}{job.created_by ? ` por ${job.created_by}` : ""} ·{" "}
            {job.params.mode === "discover" ? `${job.persons} pessoas` : `${job.matches} correspondências`} · {job.message}
          </Text>
          <Textarea label="Motivo (fica registado na auditoria)" placeholder="Ex.: parâmetros errados, análise repetida…"
            autosize minRows={2} value={reason} onChange={(e) => setReason(e.currentTarget.value)} />
          <TextInput label={`Para confirmar, escreva o número da análise: ${job.id}`} value={typed} data-autofocus
            onChange={(e) => setTyped(e.currentTarget.value)} onKeyDown={(e) => e.key === "Enter" && remove()} />
          <Group justify="flex-end">
            <Button variant="default" onClick={onClose}>Cancelar</Button>
            <Button color="red" leftSection={<IconTrash size={16} />} disabled={!confirmed} loading={busy} onClick={remove}>
              Eliminar definitivamente
            </Button>
          </Group>
        </Stack>
      )}
    </Modal>
  );
}
