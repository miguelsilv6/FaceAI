import { Alert, Anchor, Box, Group, Paper, Popover, Progress, Stack, Text, ThemeIcon, Tooltip, UnstyledButton } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconActivity, IconAlertTriangle, IconCloudUpload, IconMovie } from "@tabler/icons-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";

import { api, formatBytes, formatDuration } from "../api";
import { useAuth } from "../auth";
import type { Activity, DiskStatus } from "../types";

const POLL_MS = 4000;
const VISIBLE = 3;

interface Item {
  key: string;
  icon: ReactNode;
  color: string;
  title: string;
  detail: string;
  progress: number | null; // 0–1; null = indeterminado (em fila)
  to: string;
}

function items(a: Activity): Item[] {
  const out: Item[] = [];
  for (const j of a.jobs) {
    const queued = j.status === "queued";
    const qtext = j.held ? "retida na fila" : `em fila${j.queue_position ? ` (n.º ${j.queue_position})` : ""}`;
    out.push({
      key: `job-${j.id}`, icon: <IconActivity size={14} />, color: "indigo",
      title: `Análise #${j.id}${j.mode === "discover" ? " (descoberta)" : ""} · ${j.case_name}`,
      detail: queued ? qtext : `${Math.round(j.progress * 100)}%${j.eta_s != null ? ` · ~${formatDuration(j.eta_s)}` : ""}`,
      progress: queued ? null : j.progress, to: `/jobs/${j.id}`,
    });
  }
  for (const c of a.conversions) {
    const queued = c.status !== "running";
    out.push({
      key: `conv-${c.video_id}`, icon: <IconMovie size={14} />, color: "grape",
      title: `Cópia de reprodução · ${c.filename} · ${c.case_name}`,
      detail: queued ? "em fila" : `${Math.round(c.progress * 100)}%`,
      progress: queued ? null : c.progress, to: `/cases/${c.case_id}`,
    });
  }
  for (const u of a.uploads) {
    out.push({
      key: `up-${u.id}`, icon: <IconCloudUpload size={14} />, color: "teal",
      title: `Upload · ${u.filename} · ${u.case_name}`,
      detail: `${formatBytes(u.received_bytes)} de ${formatBytes(u.size_bytes)}`,
      progress: u.size_bytes ? u.received_bytes / u.size_bytes : null, to: `/cases/${u.case_id}`,
    });
  }
  return out;
}

function Row({ item, compact }: { item: Item; compact?: boolean }) {
  return (
    <Anchor component={Link} to={item.to} underline="never" c="inherit" style={{ minWidth: 0 }}>
      <Group gap={6} wrap="nowrap" style={{ minWidth: 0 }}>
        <ThemeIcon size="sm" variant="light" color={item.color}>{item.icon}</ThemeIcon>
        <Box style={{ minWidth: 0 }} w={compact ? 300 : 360}>
          <Group gap={6} wrap="nowrap" justify="space-between">
            <Text size="xs" fw={500} truncate="end" title={item.title}>{item.title}</Text>
            <Text size="xs" c="dimmed" style={{ whiteSpace: "nowrap" }}>{item.detail}</Text>
          </Group>
          <Progress size="xs" color={item.color} value={item.progress == null ? 100 : item.progress * 100}
            striped={item.progress == null} animated={item.progress == null} aria-label={item.title} />
        </Box>
      </Group>
    </Anchor>
  );
}

/** Faixa no topo com as tarefas em curso (análises, cópias de reprodução, uploads) dos casos acessíveis. */
export default function ActivityBar() {
  const [activity, setActivity] = useState<Activity | null>(null);
  const [, setDismissTick] = useState(0);
  const runningJobs = useRef<Set<number> | null>(null);

  useEffect(() => {
    let timer: number | undefined;
    let stopped = false;
    const tick = async () => {
      if (document.visibilityState === "visible") {
        try {
          const a = await api.activity();
          if (stopped) return;
          setActivity(a);
          if (a.disk?.level === "ok") setDiskDismissed(null); // volta a avisar se o disco piorar outra vez
          const now = new Set(a.jobs.map((j) => j.id));
          const before = runningJobs.current;
          if (before) {
            for (const id of before) {
              if (!now.has(id)) {
                api.job(id).then((job) => {
                  const ok = job.status === "completed";
                  notifications.show({
                    color: ok ? "green" : job.status === "cancelled" ? "gray" : "red",
                    title: ok ? `Análise #${id} concluída` : `Análise #${id}: ${job.status === "cancelled" ? "cancelada" : "falhou"}`,
                    message: <Anchor component={Link} to={`/jobs/${id}`} size="sm">Ver resultados</Anchor>,
                  });
                }).catch(() => undefined);
              }
            }
          }
          runningJobs.current = now;
        } catch {
          /* sem sessão ou servidor indisponível: tenta de novo no próximo ciclo */
        }
      }
      if (!stopped) timer = window.setTimeout(tick, POLL_MS);
    };
    void tick();
    const onVisible = () => {
      if (document.visibilityState === "visible") {
        window.clearTimeout(timer);
        void tick();
      }
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  const list = activity ? items(activity) : [];
  const disk = activity?.disk;
  const diskAlert = disk && disk.level !== "ok" && !diskDismissed(disk.level)
    ? <DiskAlert disk={disk} onClose={() => { setDiskDismissed(disk.level); setDismissTick((n) => n + 1); }} />
    : null;
  if (!list.length && !diskAlert) return null;
  const rest = list.slice(VISIBLE);
  return (
    // Faixa colada ao cabeçalho, de largura total (anula o padding do AppShell.Main).
    <Box mx="calc(var(--mantine-spacing-md) * -1)" mt="calc(var(--mantine-spacing-md) * -1)" mb="md"
      style={{ position: "sticky", top: "var(--app-shell-header-height, 60px)", zIndex: 150 }}>
    {diskAlert}
    {list.length > 0 && (
    <Paper withBorder radius={0} px="md" py={6} role="status" aria-label="Tarefas em curso"
      style={{ borderLeft: 0, borderRight: 0, borderTop: 0 }} bg="var(--mantine-color-body)">
      <Group gap="lg" wrap="wrap">
        <Text size="xs" fw={700} c="dimmed" tt="uppercase">Em curso ({list.length})</Text>
        {list.slice(0, VISIBLE).map((it) => <Row key={it.key} item={it} compact />)}
        {rest.length > 0 && (
          <Popover position="bottom-end" shadow="md" withArrow>
            <Popover.Target>
              <Tooltip label="Ver todas as tarefas">
                <UnstyledButton><Text size="xs" fw={600} c="indigo">+{rest.length} mais</Text></UnstyledButton>
              </Tooltip>
            </Popover.Target>
            <Popover.Dropdown>
              <Stack gap="sm">{rest.map((it) => <Row key={it.key} item={it} />)}</Stack>
            </Popover.Dropdown>
          </Popover>
        )}
      </Group>
    </Paper>
    )}
    </Box>
  );
}

const DISMISS_KEY = "faceai.diskAlertDismissed";
const LEVEL_RANK: Record<string, number> = { ok: 0, warning: 1, critical: 2 };

/** O aviso fechado fica escondido nesta sessão do browser, até o nível do disco subir. */
function diskDismissed(level: DiskStatus["level"]): boolean {
  try {
    const d = sessionStorage.getItem(DISMISS_KEY);
    return d != null && (LEVEL_RANK[d] ?? 0) >= LEVEL_RANK[level];
  } catch {
    return false;
  }
}

function setDiskDismissed(level: DiskStatus["level"] | null) {
  try {
    if (level) sessionStorage.setItem(DISMISS_KEY, level);
    else sessionStorage.removeItem(DISMISS_KEY);
  } catch {
    /* sem armazenamento: o aviso volta a aparecer no próximo ciclo */
  }
}

function DiskAlert({ disk, onClose }: { disk: DiskStatus; onClose: () => void }) {
  const { isAdmin } = useAuth();
  const critical = disk.level === "critical";
  return (
    // Fundo sólido por baixo: a variante "light" do Alert é semitransparente e a faixa é sticky.
    <Box bg="var(--mantine-color-body)">
    <Alert color={critical ? "red" : "yellow"} radius={0} py={6} icon={<IconAlertTriangle size={16} />}
      withCloseButton closeButtonLabel="Fechar aviso" onClose={onClose}
      role="alert" title={critical ? "Disco do servidor quase cheio" : "Pouco espaço em disco no servidor"}>
      <Text size="sm">
        Ocupado {disk.percent.toFixed(1)}% · livres {formatBytes(disk.free)}
        {isAdmin && disk.total ? ` de ${formatBytes(disk.total)}` : ""}.{" "}
        {critical ? "Novos uploads e análises podem ser recusados. " : ""}
        {isAdmin ? "Liberte espaço eliminando análises ou processos antigos (ver a coluna \"Espaço\" na lista de processos)."
          : "Avise o administrador."}
      </Text>
    </Alert>
    </Box>
  );
}
