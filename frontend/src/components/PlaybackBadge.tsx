import { Badge, Button, Group, Tooltip } from "@mantine/core";
import { IconRefresh } from "@tabler/icons-react";

import type { Video } from "../types";

/** Estado da cópia H.264 usada só para ver o vídeo no browser (a análise usa sempre o original). */
export default function PlaybackBadge({ video, onCreate }: { video: Video; onCreate?: (v: Video) => void }) {
  const s = video.playback_status;
  const [label, color, tip] = {
    "": ["Por avaliar", "gray", "O servidor avalia o vídeo no próximo arranque."],
    not_needed: ["Original", "gray", "O browser reproduz o ficheiro original."],
    on_demand: ["Ao abrir", "gray", "O browser não abre este formato: quando o vídeo for aberto é criada uma cópia leve"
      + " (até 720p) só para visualização, apagada ao fim de alguns dias sem uso. A análise usa sempre o original."],
    pending: ["Em fila", "yellow", "A cópia de reprodução H.264 está na fila de conversão."],
    running: [`A converter ${Math.round(video.playback_progress * 100)}%`, "blue", "A criar a cópia de reprodução H.264."],
    ready: [`Cópia ${video.playback_format === "webm" ? "VP9" : "H.264"}`, "teal",
      "O browser mostra uma cópia leve só para visualização (apagada ao fim de alguns dias sem uso e criada de novo"
      + " quando for precisa); a análise usa sempre o original."],
    failed: ["Falhou", "red", `Não foi possível criar a cópia: ${video.playback_error}`],
    unavailable: ["Sem ffmpeg", "orange", video.playback_error || "O ffmpeg não está instalado no servidor."],
  }[s] as [string, string, string];
  const canCreate = onCreate && (s === "failed" || s === "not_needed" || s === "ready" || s === "" || s === "on_demand");
  return (
    <Group gap={4} wrap="nowrap">
      <Tooltip label={tip} multiline w={280}><Badge size="sm" variant="light" color={color}>{label}</Badge></Tooltip>
      {canCreate && (
        <Tooltip label={s === "failed" ? "Tentar de novo" : "Criar (ou recriar) a cópia de reprodução H.264"}>
          <Button size="compact-xs" variant="subtle" px={4} onClick={() => onCreate(video)} aria-label="Criar cópia de reprodução">
            <IconRefresh size={14} />
          </Button>
        </Tooltip>
      )}
    </Group>
  );
}
