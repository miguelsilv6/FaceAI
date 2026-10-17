import { Alert, Button, Group, List, Modal, Stack, Text, TextInput, Textarea } from "@mantine/core";
import { IconAlertTriangle, IconTrash } from "@tabler/icons-react";
import { useEffect, useState } from "react";

import { api, formatBytes } from "../api";
import { notifyError, notifyOk } from "../notify";
import type { Case } from "../types";

interface Props {
  kase: Case | null;
  onClose: () => void;
  onDeleted: (c: Case) => void;
}

/** Confirmação da eliminação de um caso inteiro: escrever o número do caso; o motivo fica na auditoria. */
export default function DeleteCaseModal({ kase, onClose, onDeleted }: Props) {
  const [typed, setTyped] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [counts, setCounts] = useState<{ videos: number; bytes: number; refs: number; jobs: number; running: number } | null>(null);

  useEffect(() => {
    setTyped("");
    setReason("");
    setCounts(null);
    if (!kase) return;
    Promise.all([api.videos(kase.id), api.references(kase.id), api.jobs(kase.id)])
      .then(([v, r, j]) => setCounts({
        videos: v.length, bytes: v.reduce((n, x) => n + x.size_bytes, 0), refs: r.length, jobs: j.length,
        running: j.filter((x) => x.status === "running" || x.status === "queued").length,
      }))
      .catch(() => undefined);
  }, [kase?.id]);

  const confirmed = kase != null && typed.trim() === String(kase.id) && !counts?.running;
  const remove = async () => {
    if (!kase || !confirmed) return;
    setBusy(true);
    try {
      await api.deleteCase(kase.id, reason.trim());
      notifyOk(`O processo #${kase.id} foi eliminado. O registo fica na auditoria (audit.jsonl).`);
      onDeleted(kase);
    } catch (e) {
      notifyError(e, "Não foi possível eliminar o processo");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal opened={kase != null} onClose={onClose} title={kase ? `Eliminar o processo #${kase.id} — ${kase.name}?` : ""} size="lg">
      {kase && (
        <Stack>
          <Alert color="red" icon={<IconAlertTriangle size={18} />}>
            Esta ação não pode ser desfeita. São apagados do servidor:
            <List size="sm" mt={4}>
              <List.Item>
                os <b>vídeos originais</b>{counts ? ` (${counts.videos}, ${formatBytes(counts.bytes)})` : ""} e as cópias de
                reprodução;
              </List.Item>
              <List.Item>as imagens de referência{counts ? ` (${counts.refs})` : ""};</List.Item>
              <List.Item>
                as análises{counts ? ` (${counts.jobs})` : ""}, com as deteções, revisões, recortes, frames e relatórios
                gerados;
              </List.Item>
              <List.Item>os uploads por concluir.</List.Item>
            </List>
            <Text size="sm" mt={4}>
              Guarde antes os relatórios/pacotes ZIP de que precise. A auditoria mantém o histórico do processo e regista a
              eliminação (quem, quando, motivo e ficheiros apagados).
            </Text>
          </Alert>
          {counts?.running ? (
            <Alert color="orange">Há {counts.running} análise(s) a decorrer neste processo. Cancele-as antes de eliminar o processo.</Alert>
          ) : null}
          <Textarea label="Motivo (fica registado na auditoria)" autosize minRows={2} value={reason}
            placeholder="Ex.: processo arquivado, processo de teste, importação errada…" onChange={(e) => setReason(e.currentTarget.value)} />
          <TextInput label={`Para confirmar, escreva o número do processo: ${kase.id}`} value={typed} data-autofocus
            onChange={(e) => setTyped(e.currentTarget.value)} onKeyDown={(e) => e.key === "Enter" && remove()} />
          <Group justify="flex-end">
            <Button variant="default" onClick={onClose}>Cancelar</Button>
            <Button color="red" leftSection={<IconTrash size={16} />} disabled={!confirmed} loading={busy} onClick={remove}>
              Eliminar processo definitivamente
            </Button>
          </Group>
        </Stack>
      )}
    </Modal>
  );
}
