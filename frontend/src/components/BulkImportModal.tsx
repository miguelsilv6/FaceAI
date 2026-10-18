import {
  Alert, Anchor, Badge, Breadcrumbs, Button, Checkbox, Group, Loader, Modal, Paper, Progress, ScrollArea, Stack, Table,
  Tabs,
  Text, TextInput,
} from "@mantine/core";
import { IconArrowUp, IconDeviceDesktop, IconFolder, IconFolders, IconServer } from "@tabler/icons-react";
import { useEffect, useRef, useState } from "react";

import { api, formatBytes } from "../api";
import { notifyError, notifyOk } from "../notify";
import type { Location, ServerImportScan, ServerImportState } from "../types";

// Iguais a VIDEO_EXT no servidor (api/cases.py)
const VIDEO_EXT = new Set([".mp4", ".avi", ".mkv", ".mov", ".m4v", ".wmv", ".mpg", ".mpeg", ".ts", ".dav", ".h264",
  ".264", ".webm", ".flv", ".3gp", ".sec"]);
const isVideo = (name: string) => VIDEO_EXT.has(name.slice(name.lastIndexOf(".")).toLowerCase()) && !name.startsWith(".");

interface Row {
  key: string;
  name: string;
  include: boolean;
  count: number;
  bytes: number;
}

interface PickedFile {
  file: File;
  path: string; // caminho relativo à pasta escolhida/largada, ex.: "CCTV/Café Central/cam1.mp4"
}

const extOf = (name: string) => (name.includes(".") ? name.slice(name.lastIndexOf(".")).toLowerCase() : "(sem extensão)");

/** Pastas escolhidas no computador → grupos: cada subpasta (de 1.º nível) é um local; os ficheiros soltos ficam
 * num local com o nome da pasta escolhida. */
function groupFiles(files: PickedFile[]): { rows: Row[]; byKey: Map<string, File[]> } {
  const byKey = new Map<string, File[]>();
  const names = new Map<string, string>();
  for (const { file, path } of files) {
    if (!isVideo(file.name)) continue;
    const parts = path.replace(/\\/g, "/").replace(/^\/+/, "").split("/").filter(Boolean);
    if (!parts.length) parts.push(file.name);
    const key = parts.length > 2 ? parts.slice(0, 2).join("/") : parts.length === 2 ? parts[0] : "";
    names.set(key, parts.length > 2 ? parts[1] : parts.length === 2 ? parts[0] : "Ficheiros soltos");
    byKey.set(key, [...(byKey.get(key) ?? []), file]);
  }
  const rows = [...byKey.entries()].map(([key, fs]) => ({
    key, name: names.get(key) ?? key, include: true, count: fs.length, bytes: fs.reduce((n, f) => n + f.size, 0),
  })).sort((a, b) => a.name.localeCompare(b.name, "pt"));
  return { rows, byKey };
}

/** Pasta largada (arrastar e largar): percorre as entradas recursivamente. readEntries() devolve os ficheiros em
 * lotes (o Firefox e o Chrome até ~100 de cada vez): chama-se até devolver uma lista vazia. */
async function filesFromDrop(items: DataTransferItemList): Promise<PickedFile[]> {
  const out: PickedFile[] = [];
  const asFile = (e: FileSystemFileEntry) => new Promise<File>((res, rej) => e.file(res, rej));
  const readAll = async (dir: FileSystemDirectoryEntry) => {
    const reader = dir.createReader();
    const all: FileSystemEntry[] = [];
    for (;;) {
      const batch = await new Promise<FileSystemEntry[]>((res, rej) => reader.readEntries(res, rej));
      if (!batch.length) return all;
      all.push(...batch);
    }
  };
  const walk = async (entry: FileSystemEntry): Promise<void> => {
    if (entry.isFile) {
      out.push({ file: await asFile(entry as FileSystemFileEntry), path: entry.fullPath.replace(/^\/+/, "") });
    } else if (entry.isDirectory) {
      for (const child of await readAll(entry as FileSystemDirectoryEntry)) await walk(child);
    }
  };
  // As entradas têm de ser obtidas já (durante o evento "drop"); a leitura pode ser assíncrona depois.
  const entries = [...items].map((it) => it.webkitGetAsEntry?.()).filter((e): e is FileSystemEntry => !!e);
  for (const e of entries) await walk(e);
  return out;
}

const existingLoc = (locations: Location[], name: string) =>
  locations.find((l) => l.name.trim().toLocaleLowerCase() === name.trim().toLocaleLowerCase());

function PlanTable({ rows, setRows, locations }: { rows: Row[]; setRows: (r: Row[]) => void; locations: Location[] }) {
  const update = (key: string, patch: Partial<Row>) => setRows(rows.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  return (
    <ScrollArea.Autosize mah={340}>
      <Table fz="sm" verticalSpacing={4}>
        <Table.Thead>
          <Table.Tr><Table.Th w={30} /><Table.Th>Pasta</Table.Th><Table.Th>Local</Table.Th><Table.Th>Vídeos</Table.Th>
            <Table.Th>Tamanho</Table.Th></Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {rows.map((r) => {
            const ex = existingLoc(locations, r.name);
            return (
              <Table.Tr key={r.key} opacity={r.include ? 1 : 0.5}>
                <Table.Td><Checkbox checked={r.include} aria-label={`Incluir ${r.key}`}
                  onChange={(e) => update(r.key, { include: e.currentTarget.checked })} /></Table.Td>
                <Table.Td><Text size="xs" c="dimmed" maw={220} truncate title={r.key}>{r.key || "(raiz)"}</Text></Table.Td>
                <Table.Td>
                  <Group gap={6} wrap="nowrap">
                    <TextInput size="xs" w={220} value={r.name} aria-label={`Nome do local para ${r.key}`}
                      onChange={(e) => update(r.key, { name: e.currentTarget.value })} />
                    <Badge size="xs" variant="light" color={ex ? "gray" : "teal"}>{ex ? "existente" : "novo"}</Badge>
                  </Group>
                </Table.Td>
                <Table.Td>{r.count}</Table.Td>
                <Table.Td style={{ whiteSpace: "nowrap" }}>{formatBytes(r.bytes)}</Table.Td>
              </Table.Tr>
            );
          })}
        </Table.Tbody>
      </Table>
    </ScrollArea.Autosize>
  );
}

function Summary({ imported, duplicates, errors, created }: {
  imported: number; duplicates: { name: string; message: string }[]; errors: { name: string; message: string }[];
  created: string[];
}) {
  return (
    <Alert color={errors.length ? "orange" : "teal"} title={`${imported} vídeo(s) importado(s)`}>
      {created.length > 0 && <Text size="sm">Locais criados: {created.join(", ")}.</Text>}
      {duplicates.length > 0 && (
        <Text size="sm">{duplicates.length} ignorado(s) por já existirem no processo (ou repetidos na importação):
          {" "}{duplicates.slice(0, 5).map((d) => d.name).join(", ")}{duplicates.length > 5 ? "…" : ""}</Text>
      )}
      {errors.map((e) => <Text key={e.name} size="sm" c="red">{e.name}: {e.message}</Text>)}
    </Alert>
  );
}

/** Do computador: escolhe uma pasta; cada subpasta é um local. Os vídeos sobem pelo upload em blocos normal. */
function BrowserImport({ caseId, locations, onChange }: { caseId: number; locations: Location[]; onChange: () => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [byKey, setByKey] = useState<Map<string, File[]>>(new Map());
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [current, setCurrent] = useState("");
  const [result, setResult] = useState<Parameters<typeof Summary>[0] | null>(null);
  const [picked, setPicked] = useState<{ files: number; videos: number; exts: string[] } | null>(null);
  const [dragging, setDragging] = useState(false);
  useEffect(() => {
    // Só "webkitdirectory" (Firefox, Chrome, Edge, Safari). O atributo "directory" ativava no Firefox a antiga
    // Directory Upload API, em que a lista de ficheiros chega vazia.
    input.current?.setAttribute("webkitdirectory", "");
  }, []);

  const use = (files: PickedFile[]) => {
    const g = groupFiles(files);
    setRows(g.rows);
    setByKey(g.byKey);
    setResult(null);
    setPicked({ files: files.length, videos: g.rows.reduce((n, r) => n + r.count, 0),
      exts: [...new Set(files.filter((f) => !isVideo(f.file.name)).map((f) => extOf(f.file.name)))].slice(0, 12) });
  };
  const pick = (list: FileList | null) =>
    use([...(list ?? [])].map((file) => ({ file, path: file.webkitRelativePath || file.name })));
  const onDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    if (busy) return;
    try {
      const items = e.dataTransfer.items;
      const plain = [...e.dataTransfer.files].map((file) => ({ file, path: file.name }));
      const walked = items?.length && typeof items[0].webkitGetAsEntry === "function" ? await filesFromDrop(items) : [];
      use(walked.length ? walked : plain); // sem entradas (ex.: ficheiros soltos), usa a lista simples
    } catch (err) {
      notifyError(err, "Não foi possível ler a pasta largada");
    }
  };

  const chosen = rows.filter((r) => r.include && r.name.trim());
  const total = chosen.reduce((n, r) => n + r.bytes, 0) || 1;

  const run = async () => {
    setBusy(true);
    setProgress(0);
    const out = { imported: 0, duplicates: [] as { name: string; message: string }[],
      errors: [] as { name: string; message: string }[], created: [] as string[] };
    let locs = [...locations];
    let done = 0;
    for (const r of chosen) {
      setCurrent(r.name);
      try {
        let loc = existingLoc(locs, r.name);
        if (!loc) {
          try {
            loc = await api.createLocation(caseId, r.name.trim());
            out.created.push(loc.name);
          } catch (e) {
            locs = await api.locations(caseId); // ex.: criado entretanto noutro separador (409)
            loc = existingLoc(locs, r.name);
            if (!loc) throw e;
          }
          if (loc) locs = [...locs, loc];
        }
        if (!loc) throw new Error("Não foi possível criar o local.");
        const res = await api.uploadVideos(caseId, byKey.get(r.key) ?? [],
          (f) => setProgress((done + f * r.bytes) / total), loc.id);
        out.imported += res.created.length;
        out.duplicates.push(...res.duplicates.map((d) => ({ name: d.file, message: d.message })));
      } catch (e) {
        out.errors.push({ name: r.name, message: e instanceof Error ? e.message : String(e) });
      }
      done += r.bytes;
      setProgress(done / total);
    }
    setBusy(false);
    setCurrent("");
    setResult(out);
    setRows([]);
    setPicked(null);
    onChange();
    if (out.imported) notifyOk(`${out.imported} vídeo(s) importado(s) em ${chosen.length} local(is).`);
  };

  return (
    <Stack>
      <Text size="sm" c="dimmed">Escolha uma pasta no computador. Cada subpasta passa a ser um local (o nome do local é o
        nome da subpasta) com os vídeos que estiverem dentro dela, incluindo subpastas. Os vídeos soltos na pasta
        escolhida ficam num local com o nome dessa pasta. Os vídeos que já existem no processo são ignorados.</Text>
      <input ref={input} type="file" multiple hidden data-testid="bulk-folder-input"
        onChange={(e) => { pick(e.currentTarget.files); e.currentTarget.value = ""; }} />
      <Paper withBorder p="md" radius="md" data-testid="bulk-drop-zone"
        style={{ borderStyle: "dashed", borderColor: dragging ? "var(--mantine-color-indigo-5)" : undefined,
          background: dragging ? "var(--mantine-color-indigo-light)" : undefined }}
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)} onDrop={onDrop}>
        <Group justify="space-between" wrap="wrap">
          <Text size="sm">Arraste uma pasta para aqui, ou</Text>
          <Button leftSection={<IconFolders size={16} />} variant="light" disabled={busy}
            onClick={() => input.current?.click()}>Escolher pasta…</Button>
        </Group>
      </Paper>
      {picked && rows.length > 0 && (
        <Text size="sm">{rows.length} pasta(s) · {picked.videos} vídeo(s)
          {picked.files > picked.videos ? ` · ${picked.files - picked.videos} outro(s) ficheiro(s) ignorado(s)` : ""}</Text>
      )}
      {picked && rows.length === 0 && (
        <Alert color="orange" title={picked.files ? "Nenhum vídeo reconhecido na pasta" : "A pasta chegou vazia"}>
          {picked.files ? (
            <>Recebidos {picked.files} ficheiro(s), mas nenhum com uma extensão de vídeo aceite
              ({[...VIDEO_EXT].join(", ")}). Extensões encontradas: {picked.exts.join(", ") || "—"}.</>
          ) : (
            <>O browser não entregou nenhum ficheiro desta pasta. Experimente arrastar a pasta para a zona acima, ou
              confirme que o browser tem permissão para ler a pasta.</>
          )}
        </Alert>
      )}
      {rows.length > 0 && <PlanTable rows={rows} setRows={setRows} locations={locations} />}
      {busy && (
        <Stack gap={4}>
          <Progress value={progress * 100} animated striped />
          <Text size="xs" c="dimmed">A importar para "{current}"… {Math.round(progress * 100)}%. Não feche esta página
            (pode navegar na aplicação: os uploads continuam).</Text>
        </Stack>
      )}
      {result && <Summary {...result} />}
      {rows.length > 0 && (
        <Group justify="flex-end">
          <Button onClick={run} loading={busy} disabled={!chosen.length}>
            Importar {chosen.reduce((n, r) => n + r.count, 0)} vídeo(s) ({formatBytes(total)})
          </Button>
        </Group>
      )}
    </Stack>
  );
}

/** Da pasta do servidor (FACEAI_IMPORT_DIR): o servidor copia os ficheiros (os originais ficam intactos). */
function ServerImport({ caseId, locations, onChange }: { caseId: number; locations: Location[]; onChange: () => void }) {
  const [scan, setScan] = useState<ServerImportScan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [state, setState] = useState<ServerImportState | null>(null);
  const [loading, setLoading] = useState(false);

  const browse = (path: string) => {
    setLoading(true);
    api.serverImportBrowse(caseId, path).then((s) => {
      setScan(s);
      setError(null);
      setRows((s.plan ?? []).map((g) => ({ key: g.path, name: g.location_name, include: true, count: g.videos.length,
        bytes: g.videos.reduce((n, v) => n + v.size, 0) })));
    }).catch((e) => setError(e instanceof Error ? e.message : String(e))).finally(() => setLoading(false));
  };
  useEffect(() => {
    browse("");
    api.serverImportStatus(caseId).then((s) => (s.running || s.finished_at) && setState(s)).catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caseId]);
  useEffect(() => {
    if (!state?.running) return;
    const t = setTimeout(() => api.serverImportStatus(caseId).then((s) => {
      setState(s);
      if (!s.running) {
        onChange();
        if (scan) browse(scan.path ?? "");
      }
    }).catch(() => undefined), 2000);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state, caseId]);

  const start = async () => {
    try {
      const folders = rows.filter((r) => r.include && r.name.trim()).map((r) => ({
        path: r.key, location_name: r.name.trim(), recursive: r.key !== (scan?.path ?? ""),
      }));
      setState(await api.startServerImport(caseId, folders));
    } catch (e) {
      notifyError(e, "Não foi possível iniciar a importação");
    }
  };

  if (error) return <Alert color="red">{error}</Alert>;
  if (!scan) return <Loader size="sm" />;
  if (!scan.enabled) {
    return (
      <Alert color="blue" title="Pasta do servidor não configurada">
        Para importar diretamente do servidor (ex.: um disco USB ou uma partilha montada), o administrador define a
        variável <code>FACEAI_IMPORT_DIR</code> com essa pasta e reinicia o FaceAI. Ver o guia de instalação.
      </Alert>
    );
  }
  const crumbs = (scan.path ?? "").split("/").filter(Boolean);
  const chosen = rows.filter((r) => r.include && r.name.trim());
  const running = !!state?.running;
  return (
    <Stack>
      <Text size="sm" c="dimmed">Navegue até à pasta que contém uma subpasta por local. O servidor copia os vídeos para o
        processo (os originais ficam intactos), com o SHA-256 calculado durante a cópia. Os que já existem são
        ignorados.</Text>
      <Group gap="xs">
        <Button size="compact-sm" variant="default" leftSection={<IconArrowUp size={14} />}
          disabled={scan.parent == null || running} onClick={() => browse(scan.parent ?? "")}>Subir</Button>
        <Breadcrumbs separator="/">
          <Anchor size="sm" onClick={() => browse("")}>{scan.root}</Anchor>
          {crumbs.map((c, i) => (
            <Anchor key={i} size="sm" onClick={() => browse(crumbs.slice(0, i + 1).join("/"))}>{c}</Anchor>
          ))}
        </Breadcrumbs>
        {loading && <Loader size={14} />}
      </Group>
      {(scan.dirs ?? []).length > 0 && (
        <Group gap={6}>
          {(scan.dirs ?? []).map((d) => (
            <Button key={d.path} size="compact-xs" variant="light" leftSection={<IconFolder size={12} />}
              disabled={running} onClick={() => browse(d.path)}>{d.name}</Button>
          ))}
        </Group>
      )}
      {rows.length > 0 ? <PlanTable rows={rows} setRows={setRows} locations={locations} />
        : <Text size="sm" c="dimmed">Esta pasta não tem vídeos.</Text>}
      {scan.truncated && <Alert color="orange">Demasiados ficheiros: só os primeiros foram listados.</Alert>}
      {state && (state.running || state.finished_at) && (
        <Stack gap={4}>
          {state.running && (
            <>
              <Progress value={state.bytes_total ? (100 * (state.bytes_done ?? 0)) / state.bytes_total : 0} animated striped />
              <Text size="xs" c="dimmed">{state.done ?? 0} de {state.total} · {formatBytes(state.bytes_done ?? 0)} de{" "}
                {formatBytes(state.bytes_total ?? 0)} · {state.current}</Text>
            </>
          )}
          {!state.running && (
            <Summary imported={state.imported ?? 0} created={state.locations_created ?? []}
              duplicates={(state.duplicates ?? []).map((d) => ({ name: d.path, message: d.message }))}
              errors={[...(state.errors ?? []).map((d) => ({ name: d.path, message: d.message })),
                ...(state.error ? [{ name: "Importação", message: state.error }] : [])]} />
          )}
        </Stack>
      )}
      {rows.length > 0 && (
        <Group justify="flex-end">
          <Button onClick={start} loading={running} disabled={!chosen.length}>
            Importar {chosen.reduce((n, r) => n + r.count, 0)} vídeo(s) ({formatBytes(chosen.reduce((n, r) => n + r.bytes, 0))})
          </Button>
        </Group>
      )}
    </Stack>
  );
}

export default function BulkImportModal({ caseId, locations, opened, onClose, onChange }: {
  caseId: number; locations: Location[]; opened: boolean; onClose: () => void; onChange: () => void;
}) {
  return (
    <Modal opened={opened} onClose={onClose} size="xl" title="Importar pastas (cada pasta é um local)">
      <Tabs defaultValue="browser" keepMounted>
        <Tabs.List mb="md">
          <Tabs.Tab value="browser" leftSection={<IconDeviceDesktop size={14} />}>Do computador</Tabs.Tab>
          <Tabs.Tab value="server" leftSection={<IconServer size={14} />}>Da pasta do servidor</Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="browser"><BrowserImport caseId={caseId} locations={locations} onChange={onChange} /></Tabs.Panel>
        <Tabs.Panel value="server"><ServerImport caseId={caseId} locations={locations} onChange={onChange} /></Tabs.Panel>
      </Tabs>
    </Modal>
  );
}
