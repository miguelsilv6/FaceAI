import {
  ActionIcon, AppShell, Badge, Button, Group, Menu, Title, Tooltip, useComputedColorScheme, useMantineColorScheme,
} from "@mantine/core";
import { IconLogout, IconMoon, IconScan, IconSun, IconUser, IconUsers } from "@tabler/icons-react";
import { useEffect, useState } from "react";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";

import { api } from "../api";
import { useAuth } from "../auth";
import ActivityBar from "./ActivityBar";
import ErrorBoundary from "./ErrorBoundary";
import type { SystemInfo } from "../types";

export default function Layout() {
  const { user, isAdmin, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
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
            <Menu position="bottom-end" width={220}>
              <Menu.Target>
                <Button variant="default" leftSection={<IconUser size={16} />} aria-label="Menu do utilizador"
                  maw={{ base: 150, sm: 260 }}>
                  {user.display_name || user.username}
                </Button>
              </Menu.Target>
              <Menu.Dropdown>
                <Menu.Label>{user.username} · {isAdmin ? "administrador" : "analista"}</Menu.Label>
                <Menu.Item leftSection={<IconUser size={16} />} onClick={() => navigate("/account")}>
                  A minha conta</Menu.Item>
                {isAdmin && <Menu.Item leftSection={<IconUsers size={16} />} onClick={() => navigate("/users")}>
                  Utilizadores</Menu.Item>}
                <Menu.Divider />
                <Menu.Item color="red" leftSection={<IconLogout size={16} />} onClick={() => void logout()}>
                  Terminar sessão</Menu.Item>
              </Menu.Dropdown>
            </Menu>
            <ActionIcon variant="default" size="lg" aria-label="Alternar tema"
              onClick={() => setColorScheme(scheme === "dark" ? "light" : "dark")}>
              {scheme === "dark" ? <IconSun size={18} /> : <IconMoon size={18} />}
            </ActionIcon>
          </Group>
        </Group>
      </AppShell.Header>
      <AppShell.Main>
        <ActivityBar />
        <ErrorBoundary key={location.pathname}><Outlet /></ErrorBoundary>
      </AppShell.Main>
    </AppShell>
  );
}
