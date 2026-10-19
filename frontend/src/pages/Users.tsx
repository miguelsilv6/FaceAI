import {
  Badge, Button, Card, Group, Menu, Modal, PasswordInput, Select, Stack, Table, Tabs, Text, TextInput, Title,
} from "@mantine/core";
import { IconDots, IconUserPlus } from "@tabler/icons-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";

import { api, formatBytes, formatDate, usersApi } from "../api";
import { useAuth } from "../auth";
import AuditPanel from "../components/AuditPanel";
import { StorageBar, STORAGE_COLORS } from "../components/StoragePanel";
import { notifyError, notifyOk } from "../notify";
import type { AuditEvent, SystemStorage, UserAccount, UserRole } from "../types";

const ROLE_LABEL: Record<UserRole, string> = { admin: "Administrador", analyst: "Analista" };

export default function Users() {
  const { isAdmin, user: me } = useAuth();
  const [users, setUsers] = useState<UserAccount[]>([]);
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [creating, setCreating] = useState(false);
  const [resetting, setResetting] = useState<UserAccount | null>(null);

  const load = useCallback(() => {
    usersApi.list().then(setUsers).catch(notifyError);
    usersApi.systemAudit().then(setAudit).catch(notifyError);
  }, []);
  useEffect(() => {
    if (isAdmin) load();
  }, [isAdmin, load]);
  if (!isAdmin) return <Navigate to="/" replace />;

  const act = async (fn: () => Promise<unknown>, message: string) => {
    try {
      await fn();
      notifyOk(message);
      load();
    } catch (err) {
      notifyError(err);
    }
  };

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={2}>Utilizadores</Title>
        <Button leftSection={<IconUserPlus size={16} />} onClick={() => setCreating(true)}>Novo utilizador</Button>
      </Group>
      <Tabs defaultValue="users">
        <Tabs.List>
          <Tabs.Tab value="users">Contas</Tabs.Tab>
          <Tabs.Tab value="audit">Auditoria do sistema</Tabs.Tab>
          <Tabs.Tab value="storage">Armazenamento</Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="users" pt="md">
          <Card withBorder p={0}>
            <Table.ScrollContainer minWidth={760}>
              <Table striped>
                <Table.Thead>
                  <Table.Tr><Table.Th>Utilizador</Table.Th><Table.Th>Papel</Table.Th><Table.Th>Estado</Table.Th>
                    <Table.Th>Processos</Table.Th><Table.Th>Último login</Table.Th><Table.Th /></Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {users.map((u) => (
                    <Table.Tr key={u.id} opacity={u.active ? 1 : 0.55}>
                      <Table.Td><Text fw={500}>{u.username}</Text><Text size="xs" c="dimmed">{u.display_name}</Text>
                      </Table.Td>
                      <Table.Td>{ROLE_LABEL[u.role]}</Table.Td>
                      <Table.Td>
                        <Group gap={4}>
                          {!u.active && <Badge color="gray">inativo</Badge>}
                          {u.locked && <Badge color="red">bloqueado</Badge>}
                          {u.totp_enabled ? <Badge color="teal" variant="light">MFA</Badge>
                            : <Badge color="gray" variant="light">sem MFA</Badge>}
                          {u.must_change_password && <Badge color="orange">password temporária</Badge>}
                          {u.active && !u.locked && !u.must_change_password &&
                            <Badge color="green">ativo</Badge>}
                        </Group>
                      </Table.Td>
                      <Table.Td>{u.cases}</Table.Td>
                      <Table.Td>{formatDate(u.last_login_at)}</Table.Td>
                      <Table.Td>
                        <Menu position="bottom-end">
                          <Menu.Target>
                            <Button size="compact-sm" variant="subtle" aria-label={`Ações para ${u.username}`}>
                              <IconDots size={16} /></Button>
                          </Menu.Target>
                          <Menu.Dropdown>
                            <Menu.Item onClick={() => act(() => usersApi.update(u.id,
                              { role: u.role === "admin" ? "analyst" : "admin" }), "Papel alterado.")}>
                              {u.role === "admin" ? "Tornar analista" : "Tornar administrador"}
                            </Menu.Item>
                            <Menu.Item onClick={() => setResetting(u)}>Repor password…</Menu.Item>
                            <Menu.Item disabled={!u.totp_enabled} onClick={() => {
                              if (window.confirm(`Repor o MFA de ${u.username}? As sessões terminam e o MFA fica desativado até o voltar a ativar.`)) {
                                void act(() => usersApi.resetMfa(u.id), "MFA reposto.");
                              }
                            }}>Repor MFA (telemóvel perdido)</Menu.Item>
                            {u.locked && <Menu.Item onClick={() => act(() => usersApi.unlock(u.id), "Conta desbloqueada.")}>
                              Desbloquear</Menu.Item>}
                            <Menu.Divider />
                            <Menu.Item color={u.active ? "red" : undefined} disabled={u.id === me.id}
                              onClick={() => act(() => usersApi.update(u.id, { active: !u.active }),
                                u.active ? "Conta desativada." : "Conta reativada.")}>
                              {u.active ? "Desativar" : "Reativar"}
                            </Menu.Item>
                          </Menu.Dropdown>
                        </Menu>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          </Card>
          <Text size="xs" c="dimmed" mt="xs">As contas não são apagadas, só desativadas, para a auditoria continuar
            coerente. Repor a password ou o MFA termina as sessões do utilizador.</Text>
        </Tabs.Panel>
        <Tabs.Panel value="audit" pt="md">
          <AuditPanel events={audit} />
        </Tabs.Panel>
        <Tabs.Panel value="storage" pt="md">
          <SystemStoragePanel />
        </Tabs.Panel>
      </Tabs>
      <CreateUserModal opened={creating} onClose={() => setCreating(false)} onCreated={load} />
      <Modal opened={!!resetting} onClose={() => setResetting(null)} title={`Repor password de ${resetting?.username}`}>
        {resetting && <ResetPasswordForm onSubmit={async (pw) => {
          await act(() => usersApi.resetPassword(resetting.id, pw), "Password temporária definida.");
          setResetting(null);
        }} />}
      </Modal>
    </Stack>
  );
}

function ResetPasswordForm({ onSubmit }: { onSubmit: (pw: string) => Promise<void> }) {
  const [pw, setPw] = useState("");
  return (
    <form onSubmit={(e) => { e.preventDefault(); void onSubmit(pw); }}>
      <Stack>
        <PasswordInput label="Password temporária" description="Mínimo 12 caracteres. Comunique-a ao utilizador por
          um canal seguro; terá de a mudar no próximo login." autoComplete="new-password" required value={pw}
          onChange={(e) => setPw(e.currentTarget.value)} />
        <Button type="submit" disabled={pw.length < 12}>Definir</Button>
      </Stack>
    </form>
  );
}

function CreateUserModal({ opened, onClose, onCreated }: {
  opened: boolean; onClose: () => void; onCreated: () => void;
}) {
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [role, setRole] = useState<UserRole>("analyst");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await usersApi.create({ username, display_name: displayName, role, password });
      notifyOk(`Utilizador ${username} criado. No primeiro login vai mudar a password.`);
      setUsername("");
      setDisplayName("");
      setPassword("");
      setRole("analyst");
      onCreated();
      onClose();
    } catch (err) {
      notifyError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal opened={opened} onClose={onClose} title="Novo utilizador">
      <form onSubmit={submit}>
        <Stack>
          <TextInput label="Nome de utilizador" description="Minúsculas, dígitos, '.', '_' ou '-' (3–32)." required
            value={username} onChange={(e) => setUsername(e.currentTarget.value.toLowerCase())} />
          <TextInput label="Nome a apresentar" value={displayName} onChange={(e) => setDisplayName(e.currentTarget.value)} />
          <Select label="Papel" value={role} allowDeselect={false} onChange={(v) => setRole((v as UserRole) ?? "analyst")}
            data={[{ value: "analyst", label: "Analista (só os processos atribuídos)" },
              { value: "admin", label: "Administrador (todos os processos e utilizadores)" }]} />
          <PasswordInput label="Password temporária" description="Mínimo 12 caracteres; o utilizador muda-a no
            primeiro login." autoComplete="new-password" required value={password}
            onChange={(e) => setPassword(e.currentTarget.value)} />
          <Button type="submit" loading={busy}>Criar</Button>
        </Stack>
      </form>
    </Modal>
  );
}

/** Disco do servidor, totais por categoria e processos que mais ocupam. */
function SystemStoragePanel() {
  const navigate = useNavigate();
  const [data, setData] = useState<SystemStorage | null>(null);
  useEffect(() => {
    api.systemStorage().then(setData).catch(notifyError);
  }, []);
  if (!data) return <Text size="sm" c="dimmed">A calcular…</Text>;
  const d = data.disk;
  const total = data.categories.reduce((a, c) => a + c.bytes, 0);
  const regen = data.categories.filter((c) => c.regenerable).reduce((a, c) => a + c.bytes, 0);
  return (
    <Stack>
      <Card withBorder>
        <Group justify="space-between" mb="xs">
          <Title order={5}>Disco</Title>
          <Badge color={d.level === "ok" ? "green" : d.level === "warning" ? "orange" : "red"}>
            {d.percent.toFixed(0)}% usado</Badge>
        </Group>
        <Text size="sm">{formatBytes(d.free)} livres{d.total ? ` de ${formatBytes(d.total)}` : ""}</Text>
        <Text size="sm" c="dimmed" mb="xs">
          Processos: {formatBytes(total)} ({formatBytes(regen)} regenerável) · base de dados {formatBytes(data.system.database)}
          {" "}· auditoria {formatBytes(data.system.audit_log)} · uploads em curso {formatBytes(data.system.uploads_in_progress)}
        </Text>
        <StorageBar categories={data.categories} total={total} />
        <Group gap="md" mt="xs">
          {data.categories.filter((c) => c.bytes).map((c) => (
            <Group key={c.key} gap={4}>
              <div style={{ width: 10, height: 10, borderRadius: 2,
                background: `var(--mantine-color-${STORAGE_COLORS[c.key].replace(".", "-")})` }} />
              <Text size="xs">{c.label}: {formatBytes(c.bytes)}</Text>
            </Group>
          ))}
        </Group>
      </Card>
      <Card withBorder p={0}>
        <Table.ScrollContainer minWidth={520}>
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr><Table.Th>Processo</Table.Th><Table.Th ta="right">Total</Table.Th>
                <Table.Th ta="right">Regenerável</Table.Th></Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {data.cases.map((c) => (
                <Table.Tr key={c.case_id} style={{ cursor: "pointer" }} onClick={() => navigate(`/cases/${c.case_id}`)}>
                  <Table.Td>#{c.case_id} · {c.name}</Table.Td>
                  <Table.Td ta="right" ff="monospace">{formatBytes(c.total_bytes)}</Table.Td>
                  <Table.Td ta="right" ff="monospace">{formatBytes(c.regenerable_bytes)}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Card>
      <Text size="xs" c="dimmed">Para libertar espaço regenerável, abra o processo (painel → Armazenamento) ou use
        {" "}<code>python -m app.cli prune-frames</code>.</Text>
    </Stack>
  );
}
