import { Badge, Tooltip } from "@mantine/core";

interface Props {
  quality: number;
  sharpness?: number;
  sizeScore?: number;
  frontal?: number | null;
}

/** Score de qualidade da face (0–100): nitidez, tamanho e frontalidade. */
export default function QualityBadge({ quality, sharpness, sizeScore, frontal }: Props) {
  const color = quality >= 0.6 ? "teal" : quality >= 0.35 ? "yellow" : "gray";
  const pct = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 100)}`);
  return (
    <Tooltip label={`Qualidade ${pct(quality)} · nitidez ${pct(sharpness)} · tamanho ${pct(sizeScore)} · frontal ${pct(frontal)}`}>
      <Badge size="sm" variant="light" color={color}>Q {pct(quality)}</Badge>
    </Tooltip>
  );
}
