import { Alert, Button, Group, Image, Modal, NumberInput, Radio, Stack, Text, Textarea } from "@mantine/core";
import { IconInfoCircle } from "@tabler/icons-react";
import { useState } from "react";

import { api } from "../api";
import { notifyError, notifyOk } from "../notify";
import type { Job, Video } from "../types";
import { aspectCorrected, displaySize } from "../videoAspect";

type Choice = "auto" | "recorded" | "suggested" | "16:9" | "4:3" | "custom";

/** Mesma regra do servidor (services/aspect.py): a proporção pedida sem reduzir nenhum dos lados. */
function sizeForRatio(w: number, h: number, ratio: number): [number, number] {
  const even = (n: number) => Math.max(2, Math.round(n / 2) * 2);
  return h * ratio >= w ? [even(h * ratio), even(h)] : [even(w), even(w / ratio)];
}

/** Proporção de apresentação de um vídeo com píxeis não quadrados (o ficheiro não muda). */
export default function AspectModal({ video, jobs, onClose, onSaved }: {
  video: Video; jobs: Job[]; onClose: () => void; onSaved: (v: Video) => void;
}) {
  const suggested = video.aspect?.suggested;
  const current = displaySize(video);
  const manual = video.aspect?.source === "manual";
  const [choice, setChoice] = useState<Choice>(!manual ? "auto" : aspectCorrected(video) ? "custom" : "recorded");
  const [custom, setCustom] = useState<[number | string, number | string]>(current);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const usedBy = jobs.filter((j) => j.video_ids.includes(video.id));

  // "Automática": o servidor volta a aplicar a regra (SAR do ficheiro ou modo de DVR sem ambiguidade)
  const autoSize: [number, number] = !manual ? current : suggested?.display ?? [video.width, video.height];
  const size: [number, number] | null = choice === "auto" ? autoSize : choice === "recorded" ? null
    : choice === "suggested" && suggested ? suggested.display
      : choice === "16:9" ? sizeForRatio(video.width, video.height, 16 / 9)
        : choice === "4:3" ? sizeForRatio(video.width, video.height, 4 / 3)
          : [Number(custom[0]) || 0, Number(custom[1]) || 0];
  const shown = size ?? [video.width, video.height];
  const validSize = shown[0] >= 16 && shown[1] >= 16 && shown[0] <= 8192 && shown[1] <= 8192;
  const changed = choice === "auto" ? manual : shown[0] !== current[0] || shown[1] !== current[1];

  const save = async () => {
    setBusy(true);
    try {
      const v = await api.setAspect(video.id, choice === "auto" ? null : size, reason.trim(), choice === "auto");
      notifyOk(choice === "auto" ? "Proporção automática reposta. Registado na auditoria."
        : size ? `Proporção corrigida: ${size[0]}×${size[1]}. Registado na auditoria.`
          : "Proporção reposta (a gravada). Registado na auditoria.");
      onSaved(v);
    } catch (e) {
      notifyError(e, "Não foi possível alterar a proporção");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal opened onClose={onClose} title={`Proporção do vídeo #${video.id}`} size="lg">
      <Stack>
        <Text size="sm">
          {video.filename}: gravado em <b>{video.width}×{video.height}</b>, mostrado como <b>{current.join("×")}</b>
          {video.aspect?.source ? ` (${video.aspect.source === "manual" ? `escolhido por ${video.aspect.set_by}` : video.aspect.source})` : ""}.
          Alguns DVR gravam com píxeis que não são quadrados (modos "Lite"/meia resolução): a proporção é ajustada
          automaticamente quando é conhecida; altere-a aqui se a imagem continuar deformada.
        </Text>
        <Image src={api.videoFrameUrl(video.id, Math.min(5, video.duration_s / 2 || 0))} alt="Pré-visualização"
          style={{ aspectRatio: `${shown[0]} / ${shown[1]}`, objectFit: "fill", maxHeight: 260, width: "auto",
            maxWidth: "100%", margin: "0 auto", background: "#000" }} />
        <Radio.Group value={choice} onChange={(v) => setChoice(v as Choice)} label="Mostrar como">
          <Stack gap={6} mt={6}>
            <Radio value="auto" label={!manual ? `Automática (atual: ${current.join("×")})`
              : "Automática (o FaceAI decide pelo ficheiro ou pelo modo de DVR)"} />
            {suggested && (suggested.display[0] !== current[0] || suggested.display[1] !== current[1]) && (
              <Radio value="suggested"
                label={`Sugerida: ${suggested.display[0]}×${suggested.display[1]} (${suggested.mode})`} />
            )}
            <Radio value="16:9" label={`16:9 (${sizeForRatio(video.width, video.height, 16 / 9).join("×")})`} />
            <Radio value="4:3" label={`4:3 (${sizeForRatio(video.width, video.height, 4 / 3).join("×")})`} />
            <Radio value="custom" label="Tamanho à medida" />
            <Radio value="recorded" label={`Como foi gravado (${video.width}×${video.height})`} />
          </Stack>
        </Radio.Group>
        {choice === "custom" && (
          <Group grow>
            <NumberInput label="Largura" min={16} max={8192} allowDecimal={false} value={custom[0]} onChange={(v) => setCustom([v, custom[1]])} />
            <NumberInput label="Altura" min={16} max={8192} allowDecimal={false} value={custom[1]} onChange={(v) => setCustom([custom[0], v])} />
          </Group>
        )}
        <Textarea label="Motivo (fica registado na auditoria)" autosize minRows={2} value={reason}
          placeholder="Ex.: o DVR grava em 1080P Lite; no leitor do fabricante a imagem é 16:9"
          onChange={(e) => setReason(e.currentTarget.value)} />
        <Alert color="blue" icon={<IconInfoCircle size={18} />}>
          O ficheiro e o SHA-256 não mudam. O leitor e as <b>novas análises</b> passam a usar este tamanho (as faces
          ficam com a forma real, o que melhora a deteção e o reconhecimento).
          {usedBy.length > 0 && <> As análises {usedBy.map((j) => `#${j.id}`).join(", ")} mantêm o tamanho com que
            foram feitas: repita-as para aplicar a nova proporção.</>}
        </Alert>
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>Cancelar</Button>
          <Button disabled={!validSize || !changed || reason.trim().length < 3} loading={busy} onClick={save}>
            Aplicar
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
