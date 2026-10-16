import {
  Alert, Badge, Box, Button, Group, Modal, NumberInput, ScrollArea, Select, Slider, Stack, Table, Tabs, Text, TextInput,
} from "@mantine/core";
import { IconAlertTriangle, IconClock, IconScan, IconWand } from "@tabler/icons-react";
import { useEffect, useRef, useState } from "react";

import { ApiError, api, formatTime, realTime } from "../api";
import { notifyError, notifyOk } from "../notify";
import { cameraLabel } from "../places";
import type { ClockCalibration, Location, OcrTest, Video } from "../types";

interface Props {
  video: Video;
  opened: boolean;
  onClose: () => void;
  onSaved: (v: Video) => void;
  locations?: Location[];
}

type Roi = [number, number, number, number];

const DATE_ORDERS = [
  { value: "DMY", label: "Dia-Mês-Ano (31-12-2026)" },
  { value: "MDY", label: "Mês-Dia-Ano (12/31/2026)" },
  { value: "YMD", label: "Ano-Mês-Dia (2026-12-31)" },
];

/**
 * Acerto da hora real de um vídeo: OCR da data/hora impressa na imagem (zona automática ou marcada com o rato),
 * acerto manual a partir de um frame, e desvio do relógio do DVR.
 */
export default function ClockModal({ video, opened, onClose, onSaved, locations = [] }: Props) {
  const [t, setT] = useState(0);
  const [roi, setRoi] = useState<Roi | null>((video.time_calibration as ClockCalibration)?.roi as Roi ?? null);
  const [drag, setDrag] = useState<{ x: number; y: number } | null>(null);
  const [dateOrder, setDateOrder] = useState((video.time_calibration as ClockCalibration)?.date_order ?? "DMY");
  const [samples, setSamples] = useState(12);
  const [offset, setOffset] = useState(video.clock_offset_s || 0);
  const [note, setNote] = useState(video.time_note || "");
  const [test, setTest] = useState<OcrTest | null>(null);
  const [calibration, setCalibration] = useState<ClockCalibration | null>(
    video.time_source === "ocr" ? (video.time_calibration as ClockCalibration) : null);
  const [manual, setManual] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (test?.dvr && !manual) setManual(test.dvr.replace("T", " "));
  }, [test, manual]);

  const point = (e: React.MouseEvent) => {
    const r = boxRef.current!.getBoundingClientRect();
    return { x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)),
      y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)) };
  };
  const onDown = (e: React.MouseEvent) => {
    e.preventDefault();
    const p = point(e);
    setDrag(p);
    setRoi([p.x, p.y, 0, 0]);
  };
  const onMove = (e: React.MouseEvent) => {
    if (!drag) return;
    const p = point(e);
    setRoi([Math.min(drag.x, p.x), Math.min(drag.y, p.y), Math.abs(p.x - drag.x), Math.abs(p.y - drag.y)]);
  };
  const onUp = () => {
    setDrag(null);
    if (roi && (roi[2] < 0.01 || roi[3] < 0.01)) setRoi(null);
  };

  const run = async (label: string, fn: () => Promise<void>) => {
    setBusy(label);
    try {
      await fn();
    } catch (e) {
      if (e instanceof ApiError && e.detail && typeof e.detail === "object" && "calibration" in e.detail) {
        setCalibration((e.detail as { calibration: ClockCalibration }).calibration);
      }
      notifyError(e, "Hora real");
    } finally {
      setBusy(null);
    }
  };

  const detect = () => run("detect", async () => {
    const r = await api.clockOcrTest(video.id, { roi: null, t, date_order: dateOrder });
    setTest(r);
    if (r.roi) setRoi(r.roi as Roi);
    else notifyError("Não foi encontrada nenhuma data/hora neste frame. Marque a zona com o rato.", "OCR");
  });
  const testRoi = () => run("test", async () => {
    setTest(await api.clockOcrTest(video.id, { roi, t, date_order: dateOrder }));
  });
  const calibrate = () => run("ocr", async () => {
    const v = await api.clockOcr(video.id, { roi, samples, date_order: dateOrder, clock_offset_s: offset, note });
    const cal = v.time_calibration as ClockCalibration;
    setCalibration(cal);
    notifyOk(`Hora acertada por OCR: início ${realTime(v, 0)} · ${cal.inliers ?? "?"}/${cal.samples?.length ?? "?"} leituras `
      + `consistentes · erro máx. ${cal.max_residual_s ?? "?"} s`);
    onSaved(v);
    onClose();
  });
  const saveManual = () => run("manual", async () => {
    const dvr = manual.trim().replace(" ", "T");
    const v = await api.clockManual(video.id, { video_s: t, dvr_time: dvr, clock_offset_s: offset, note });
    notifyOk(`Hora acertada manualmente: início ${realTime(v, 0)}`);
    onSaved(v);
    onClose();
  });
  const saveOffset = () => run("offset", async () => {
    onSaved(await api.clockOffset(video.id, offset, note));
    notifyOk("Desvio atualizado.");
    onClose();
  });
  const reset = () => run("reset", async () => {
    onSaved(await api.clockReset(video.id));
    setCalibration(null);
    notifyOk("Hora real removida.");
    onClose();
  });

  const pct = (v: number) => `${v * 100}%`;
  return (
    <Modal opened={opened} onClose={onClose} size="xl" title={`Hora real — ${cameraLabel(video, locations)}`}>
      <Stack>
        {video.dvr_start ? (
          <Alert color="green" icon={<IconClock size={18} />}>
            Início: <b>{realTime(video, 0)}</b> · fim: <b>{realTime(video, video.duration_s)}</b> ·{" "}
            {video.time_source === "ocr" ? "OCR" : "manual"}
            {video.dvr_rate !== 1 ? ` · taxa ${video.dvr_rate.toFixed(4)}` : ""}
            {video.clock_offset_s ? ` · desvio ${video.clock_offset_s > 0 ? "+" : ""}${video.clock_offset_s} s` : ""}
          </Alert>
        ) : (
          <Text size="sm" c="dimmed">
            Este vídeo ainda não tem hora real. Os resultados mostram só o tempo relativo ao início do ficheiro.
          </Text>
        )}

        <Box ref={boxRef} pos="relative" style={{ cursor: "crosshair", userSelect: "none", lineHeight: 0 }}
          onMouseDown={onDown} onMouseMove={onMove} onMouseUp={onUp} onMouseLeave={onUp}>
          <img src={api.videoFrameUrl(video.id, t)} alt="Frame" draggable={false}
            style={{ width: "100%", borderRadius: 6, background: "#000" }} />
          {roi && (
            <Box pos="absolute" left={pct(roi[0])} top={pct(roi[1])} w={pct(roi[2])} h={pct(roi[3])}
              style={{ border: "2px solid var(--mantine-color-yellow-5)", background: "rgba(250, 200, 0, 0.12)",
                pointerEvents: "none" }} />
          )}
        </Box>
        <Group gap="xs" wrap="nowrap">
          <Text size="xs" ff="monospace" w={110}>{formatTime(t)}</Text>
          <Slider style={{ flex: 1 }} min={0} max={Math.max(0, video.duration_s - 0.5)} step={0.5} value={t}
            onChange={setT} label={(v) => formatTime(v)} />
        </Group>
        <Text size="xs" c="dimmed">
          Arraste o rato sobre a imagem para marcar a zona da data/hora, ou use a deteção automática.
        </Text>

        <Group grow>
          <Select label="Formato da data" data={DATE_ORDERS} value={dateOrder} allowDeselect={false}
            onChange={(v) => v && setDateOrder(v)} />
          <NumberInput label="Desvio do relógio do DVR (s)" value={offset} step={1} decimalScale={1}
            description="DVR adiantado X s → −X" onChange={(v) => setOffset(typeof v === "number" ? v : Number(v) || 0)} />
          <TextInput label="Nota" placeholder="ex.: relógio confirmado com o operador" value={note}
            onChange={(e) => setNote(e.currentTarget.value)} />
        </Group>

        <Tabs defaultValue="ocr">
          <Tabs.List>
            <Tabs.Tab value="ocr" leftSection={<IconScan size={16} />}>OCR da imagem</Tabs.Tab>
            <Tabs.Tab value="manual" leftSection={<IconClock size={16} />}>Acerto manual</Tabs.Tab>
          </Tabs.List>
          <Tabs.Panel value="ocr" pt="sm">
            <Stack>
              <Group>
                <Button variant="light" leftSection={<IconWand size={16} />} loading={busy === "detect"} onClick={detect}>
                  Detetar automaticamente
                </Button>
                <Button variant="light" disabled={!roi} loading={busy === "test"} onClick={testRoi}>
                  Testar zona neste frame
                </Button>
                <NumberInput w={120} label="Amostras" min={2} max={60} value={samples}
                  onChange={(v) => setSamples(typeof v === "number" ? v : 12)} />
                <Button loading={busy === "ocr"} onClick={calibrate} style={{ alignSelf: "flex-end" }}>
                  Calibrar por OCR
                </Button>
              </Group>
              {test && (
                <Text size="sm">
                  Lido: <code>{test.text || "—"}</code> →{" "}
                  {test.dvr ? <Badge color="green" variant="light">{test.dvr.replace("T", " ")}</Badge>
                    : <Badge color="red" variant="light">sem data/hora reconhecida</Badge>}
                </Text>
              )}
              {calibration?.samples && (
                <>
                  {calibration.error ? (
                    <Alert color="red" icon={<IconAlertTriangle size={18} />}>{calibration.error}</Alert>
                  ) : (
                    <Text size="sm">
                      {calibration.inliers}/{calibration.samples.length} leituras consistentes · erro máx.{" "}
                      {calibration.max_residual_s} s{calibration.rate && calibration.rate !== 1
                        ? ` · taxa ${calibration.rate.toFixed(4)} (fps nominal ≠ tempo real)` : ""}
                    </Text>
                  )}
                  {/* Scroll vertical (o ScrollContainer só faz scroll na horizontal e cortava as linhas). */}
                  <ScrollArea.Autosize mah={360} type="auto" offsetScrollbars>
                  <Table.ScrollContainer minWidth={500}>
                    <Table fz="xs" striped stickyHeader>
                      <Table.Thead>
                        <Table.Tr><Table.Th>Vídeo</Table.Th><Table.Th>Texto lido</Table.Th><Table.Th>Data/hora</Table.Th>
                          <Table.Th /></Table.Tr>
                      </Table.Thead>
                      <Table.Tbody>
                        {calibration.samples.map((s, i) => (
                          <Table.Tr key={i}>
                            <Table.Td ff="monospace">{formatTime(s.video_s)}</Table.Td>
                            <Table.Td><code>{s.text}</code></Table.Td>
                            <Table.Td>
                              {s.dvr ? s.dvr.replace("T", " ") : "—"}
                              {s.date_inferred && (
                                <Badge size="xs" ml={4} variant="light" color="yellow"
                                  title="Só a hora era legível neste frame; a data veio das outras leituras">data inferida</Badge>
                              )}
                            </Table.Td>
                            <Table.Td>{s.inlier ? <Badge size="xs" color="green">ok</Badge>
                              : <Badge size="xs" color="gray">descartada</Badge>}</Table.Td>
                          </Table.Tr>
                        ))}
                      </Table.Tbody>
                    </Table>
                  </Table.ScrollContainer>
                  </ScrollArea.Autosize>
                </>
              )}
            </Stack>
          </Tabs.Panel>
          <Tabs.Panel value="manual" pt="sm">
            <Group align="flex-end">
              <TextInput style={{ flex: 1 }} label={`Data/hora do DVR no frame ${formatTime(t)}`}
                placeholder="AAAA-MM-DD HH:MM:SS" value={manual} onChange={(e) => setManual(e.currentTarget.value)}
                error={manual && !/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(\.\d+)?$/.test(manual.trim())
                  ? "Formato: AAAA-MM-DD HH:MM:SS" : undefined} />
              <Button loading={busy === "manual"} onClick={saveManual}
                disabled={!/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(\.\d+)?$/.test(manual.trim())}>
                Guardar acerto manual
              </Button>
            </Group>
          </Tabs.Panel>
        </Tabs>

        {video.dvr_start && (
          <Group justify="space-between">
            <Button variant="subtle" color="red" loading={busy === "reset"} onClick={reset}>Remover hora real</Button>
            <Button variant="default" loading={busy === "offset"} onClick={saveOffset}>Guardar só o desvio e a nota</Button>
          </Group>
        )}
      </Stack>
    </Modal>
  );
}
