import {
  ActionIcon, AppShell, Badge, Group, TextInput, Title, Tooltip, useComputedColorScheme, useMantineColorScheme,
} from "@mantine/core";
import { IconMoon, IconScan, IconSun, IconUser } from "@tabler/icons-react";
import { useEffect, useState } from "react";
import { Link, Outlet } from "react-router-dom";

import { api, getAnalyst, setAnalyst } from "../api";
import type { SystemInfo } from "../types";

export default function Layout() {
  const [analyst, setAnalystState] = useState(getAnalyst());
  const [system, setSystem] = useState<SystemInfo | null>(null);
  const { setColorScheme } = useMantineColorScheme();
  const scheme = useComputedColorScheme("light");

  useEffect(() => {
    api.system().then(setSystem).catch(() => setSystem(null));
  }, []);

  const env = system?.environment;
  return (
    <AppShell header={{ height: 60 }} padding="md">
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between" wrap="nowrap">
          <Link to="/" style={{ textDecoration: "none", color: "inherit" }}>
            <Group gap="xs" wrap="nowrap">
              <IconScan size={28} />
              <Title order={3}>FaceAI</Title>
              <Badge variant="light" visibleFrom="sm">análise facial em CCTV</Badge>
            </Group>
          </Link>
          <Group gap="sm" wrap="nowrap">
            {env && (
              <Tooltip label={`DeepFace ${env.deepface} · TensorFlow ${env.tensorflow} · OpenCV ${env.opencv}`}>
                <Badge color={env.device === "GPU" ? "green" : "gray"} visibleFrom="xs">{String(env.device)}</Badge>
              </Tooltip>
            )}
            <TextInput
              aria-label="Nome do analista"
              placeholder="Nome do analista"
              leftSection={<IconUser size={16} />}
              value={analyst}
              onChange={(e) => {
                setAnalystState(e.currentTarget.value);
                setAnalyst(e.currentTarget.value);
              }}
              w={{ base: 150, sm: 220 }}
            />
            <ActionIcon variant="default" size="lg" aria-label="Alternar tema"
              onClick={() => setColorScheme(scheme === "dark" ? "light" : "dark")}>
              {scheme === "dark" ? <IconSun size={18} /> : <IconMoon size={18} />}
            </ActionIcon>
          </Group>
        </Group>
      </AppShell.Header>
      <AppShell.Main>
        <Outlet />
      </AppShell.Main>
    </AppShell>
  );
}
