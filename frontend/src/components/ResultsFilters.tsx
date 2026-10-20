import { Badge, Button, Popover, SegmentedControl, Slider, Stack, Switch, Text } from "@mantine/core";
import { IconFilter } from "@tabler/icons-react";

export type ReviewFilter = "all" | "pending" | "confirmed";
export type Order = "time" | "score" | "quality";

/** Filtros e ordenação da grelha de resultados, reunidos num só botão. */
export default function ResultsFilters({
  discover, clothingView, review, onReview, minSim, onMinSim, showCandidates, onShowCandidates,
  showRejected, onShowRejected, rejectedCount, order, onOrder, multiPlace, onMultiPlace,
}: {
  discover: boolean;
  clothingView: boolean;
  review: ReviewFilter;
  onReview: (v: ReviewFilter) => void;
  minSim: number;
  onMinSim: (v: number) => void;
  showCandidates: boolean;
  onShowCandidates: (v: boolean) => void;
  showRejected: boolean;
  onShowRejected: (v: boolean) => void;
  rejectedCount: number;
  order: Order;
  onOrder: (v: Order) => void;
  multiPlace?: boolean; // vista Pessoas (descoberta): só as vistas em vários locais
  onMultiPlace?: (v: boolean) => void;
}) {
  const similarity = !discover && !clothingView;
  const active = Number(review !== "all") + Number(similarity && minSim > 0) + Number(similarity && showCandidates)
    + Number(showRejected) + Number(order !== "time") + Number(!!multiPlace);
  const reset = () => {
    onReview("all");
    onMinSim(0);
    onShowCandidates(false);
    onShowRejected(false);
    onOrder("time");
    onMultiPlace?.(false);
  };
  return (
    <Popover width={300} position="bottom-end" withArrow shadow="md">
      <Popover.Target>
        <Button size="xs" variant={active ? "light" : "default"} leftSection={<IconFilter size={14} />}
          rightSection={active ? <Badge size="xs" circle>{active}</Badge> : undefined}>
          Filtros
        </Button>
      </Popover.Target>
      <Popover.Dropdown>
        <Stack gap="sm">
          <div>
            <Text size="xs" fw={500} mb={4}>Estado da revisão</Text>
            <SegmentedControl size="xs" fullWidth value={review} onChange={(v) => onReview(v as ReviewFilter)}
              data={[{ value: "all", label: "Todas" }, { value: "pending", label: "Por rever" },
                { value: "confirmed", label: "Confirmadas" }]} />
          </div>
          {similarity && (
            <>
              <div>
                <Text size="xs" fw={500}>Semelhança mínima: {(minSim * 100).toFixed(0)}%</Text>
                <Slider min={0} max={1} step={0.01} value={minSim} onChange={onMinSim} label={null} />
              </div>
              <Switch size="xs" label="Mostrar candidatos abaixo do limiar" checked={showCandidates}
                onChange={(e) => onShowCandidates(e.currentTarget.checked)} />
            </>
          )}
          {multiPlace !== undefined && onMultiPlace && (
            <Switch size="xs" label="Só pessoas vistas em vários locais" checked={multiPlace}
              onChange={(e) => onMultiPlace(e.currentTarget.checked)} />
          )}
          <Switch size="xs" label={`Mostrar rejeitadas (${rejectedCount})`} checked={showRejected}
            onChange={(e) => onShowRejected(e.currentTarget.checked)} />
          <div>
            <Text size="xs" fw={500} mb={4}>Ordenar por</Text>
            <SegmentedControl size="xs" fullWidth value={order} onChange={(v) => onOrder(v as Order)}
              data={[{ value: "time", label: "Tempo" }, { value: "score", label: discover ? "Presença" : "Semelhança" },
                { value: "quality", label: "Qualidade" }]} />
          </div>
          <Button size="compact-xs" variant="subtle" disabled={!active} onClick={reset}>Repor filtros</Button>
        </Stack>
      </Popover.Dropdown>
    </Popover>
  );
}
