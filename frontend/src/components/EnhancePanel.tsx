import { Badge, Button, Chip, Group, Image, SegmentedControl, SimpleGrid, Slider, Stack, Text } from "@mantine/core";
import { IconDownload } from "@tabler/icons-react";
import { useState } from "react";

import { api } from "../api";

const OPS = [
  { value: "clahe", label: "Contraste (CLAHE)" },
  { value: "sharpen", label: "Nitidez" },
  { value: "denoise", label: "Reduzir ruído" },
];

/** Melhoria de imagem só para visualização (operações clássicas, determinísticas). O original não é alterado. */
export default function EnhancePanel({ detectionId }: { detectionId: number }) {
  const [src, setSrc] = useState<"crop" | "original">("crop");
  const [ops, setOps] = useState<string[]>(["clahe"]);
  const [upscale, setUpscale] = useState(2);
  const [gamma, setGamma] = useState(1);
  const opts = { src, ops, upscale, gamma };
  const originalUrl = src === "crop" ? api.detectionImageUrl(detectionId, "crop") : api.detectionFrameUrl(detectionId, "original");
  return (
    <Stack gap="sm">
      <Group justify="space-between" wrap="wrap">
        <SegmentedControl size="xs" value={src} onChange={(v) => setSrc(v as "crop" | "original")}
          data={[{ value: "crop", label: "Face" }, { value: "original", label: "Frame completo" }]} />
        <Badge color="orange" variant="filled">Imagem melhorada — só para visualização</Badge>
      </Group>
      <Group gap="lg" wrap="wrap" align="flex-end">
        <Chip.Group multiple value={ops} onChange={setOps}>
          <Group gap={6}>{OPS.map((o) => <Chip key={o.value} size="xs" value={o.value}>{o.label}</Chip>)}</Group>
        </Chip.Group>
        <SegmentedControl size="xs" value={String(upscale)} onChange={(v) => setUpscale(Number(v))}
          data={[{ value: "1", label: "1×" }, { value: "2", label: "2×" }, { value: "4", label: "4×" }]} />
        <Stack gap={2} w={180}>
          <Text size="xs">Gama {gamma.toFixed(2)} {gamma < 1 ? "(clareia sombras)" : gamma > 1 ? "(escurece)" : ""}</Text>
          <Slider min={0.4} max={2} step={0.05} value={gamma} onChange={setGamma} label={null} />
        </Stack>
      </Group>
      <SimpleGrid cols={2} spacing="xs">
        <Stack gap={2}>
          <Text size="xs" c="dimmed">Original</Text>
          <Image src={originalUrl} alt="Original" fit="contain" mah={src === "crop" ? 360 : 480}
            style={{ imageRendering: src === "crop" ? "pixelated" : undefined }} />
        </Stack>
        <Stack gap={2}>
          <Text size="xs" c="dimmed">Melhorada</Text>
          <Image key={api.enhancedUrl(detectionId, opts)} src={api.enhancedUrl(detectionId, opts)} alt="Imagem melhorada"
            fit="contain" mah={src === "crop" ? 360 : 480} />
        </Stack>
      </SimpleGrid>
      <Group justify="space-between" wrap="wrap">
        <Text size="xs" c="dimmed" maw={560}>
          Ampliação Lanczos, contraste adaptativo, gama, redução de ruído e nitidez: não acrescentam detalhe (sem IA
          generativa). Serve só para ajudar a rever; relatórios e exportações usam sempre as imagens originais.
        </Text>
        <Button component="a" size="xs" variant="light" leftSection={<IconDownload size={16} />}
          href={api.enhancedUrl(detectionId, opts, true)}>Descarregar melhorada (marcada)</Button>
      </Group>
    </Stack>
  );
}
