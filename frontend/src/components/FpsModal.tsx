import { Alert, Button, Group, Modal, NumberInput, Stack, Text, Textarea } from "@mantine/core";
import { IconAlertTriangle, IconClock } from "@tabler/icons-react";
import { useState } from "react";

import { api } from "../api";
import { notifyError, notifyOk } from "../notify";
import type { Job, Video } from "../types";

/** Correção do fps de um vídeo extraído (.sec): os frames não mudam, só o ritmo e os tempos das análises. */
export default function FpsModal({ video, jobs, onClose, onSaved }: {
  video: Video; jobs: Job[]; onClose: () => void; onSaved: (v: Video) => void;
}) {
  const x = video.extraction ?? {};
  const suggestion = x.suggested_fps;
  const [fps, setFps] = useState<number | string>(suggestion?.fps ?? video.fps);
  const [reason, setReason] = useState("");
  const [fromOcr, setFromOcr] = useState(false);
  const [busy, setBusy] = useState(false);
  const usedBy = jobs.filter((j) => j.video_ids.includes(video.id));
  const value = typeof fps === "number" ? fps : Number(fps);
  const valid = value >= 0.5 && value <= 60 && Math.abs(value - video.fps) > 1e-6 && reason.trim().length >= 3;
  const save = async () => {
    setBusy(true);
    try {
      const v = await api.correctFps(video.id, value, reason.trim(), fromOcr ? "ocr" : "manual");
      notifyOk(`fps corrigido para ${value}: duração ${Math.round(v.duration_s)} s. Registado na auditoria.`);
      onSaved(v);
    } catch (e) {
      notifyError(e, "Não foi possível corrigir o fps");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal opened onClose={onClose} title={`fps do vídeo #${video.id}`} size="lg">
      <Stack>
        <Text size="sm">
          {video.filename}: <b>{video.fps} fps</b> ({x.fps_source ?? "?"}), {video.frame_count} frames.
          O ficheiro .sec não indica o instante de cada frame: se o DVR gravou a outro ritmo, o vídeo reproduz-se
          depressa (ou devagar) e a duração e os tempos ficam errados na mesma proporção.
        </Text>
        {suggestion && (
          <Alert color="blue" icon={<IconClock size={18} />}>
            O relógio do DVR (OCR) avança {suggestion.rate.toFixed(2)} s por cada segundo do vídeo: o fps real é
            ≈ <b>{suggestion.fps}</b>.{" "}
            <Button size="compact-xs" variant="light" onClick={() => {
              setFps(suggestion.fps);
              setFromOcr(true);
              if (!reason) setReason("fps indicado pelo relógio do DVR (OCR)");
            }}>Usar</Button>
          </Alert>
        )}
        <NumberInput label="fps real" min={0.5} max={60} step={1} decimalScale={3} value={fps}
          onChange={(v) => { setFps(v); setFromOcr(false); }} />
        <Textarea label="Motivo (fica registado na auditoria)" autosize minRows={2} value={reason}
          placeholder="Ex.: o DVR grava a 12 fps (configuração do gravador)"
          onChange={(e) => setReason(e.currentTarget.value)} />
        <Alert color="orange" icon={<IconAlertTriangle size={18} />}>
          A cópia de análise é refeita com o novo ritmo (mesma sequência H.264, sem recodificação) e a cópia de
          reprodução é criada de novo.
          {usedBy.length > 0 && <> Os tempos das análises {usedBy.map((j) => `#${j.id}`).join(", ")} são
            recalculados (n.º do frame ÷ fps); os frames analisados não mudam.</>}
        </Alert>
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>Cancelar</Button>
          <Button disabled={!valid} loading={busy} onClick={save}>Corrigir fps</Button>
        </Group>
      </Stack>
    </Modal>
  );
}
