import {
  Alert, Badge, Button, Card, Group, Modal, PasswordInput, SimpleGrid, Stack, Table, Text, Title,
} from "@mantine/core";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { authApi, formatDate, setCsrfToken } from "../api";
import { useAuth } from "../auth";
import { notifyError, notifyOk } from "../notify";
import type { UserSessionInfo } from "../types";
import { PasswordFields, RecoveryCodesBody } from "./Login";

export default function Account() {
  const { user, refresh } = useAuth();
  const [sessions, setSessions] = useState<UserSessionInfo[]>([]);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [codesPassword, setCodesPassword] = useState("");
  const [codesOpen, setCodesOpen] = useState(false);
  const [codes, setCodes] = useState<string[] | null>(null);

  const loadSessions = useCallback(() => {
    authApi.sessions().then(setSessions).catch(notifyError);
  }, []);
  useEffect(loadSessions, [loadSessions]);

  const changePassword = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      const s = await authApi.changePassword(current, next);
      setCsrfToken(s.csrf_token);
      setCurrent("");
      setNext("");
      setConfirm("");
      notifyOk("Password alterada. As outras sessões foram terminadas.");
      await refresh();
      loadSessions();
    } catch (err) {
      notifyError(err);
    } finally {
      setBusy(false);
    }
  };

  const regenerate = async () => {
    try {
      setCodes((await authApi.regenerateRecoveryCodes(codesPassword)).recovery_codes);
      setCodesPassword("");
    } catch (err) {
      notifyError(err);
    }
  };

  return (
    <Stack>
      <Title order={2}>A minha conta</Title>
      <SimpleGrid cols={{ base: 1, md: 2 }}>
        <Card withBorder>
          <Stack gap="xs">
            <Group justify="space-between">
              <Text fw={600}>{user.display_name || user.username}</Text>
              <Badge variant="light">{user.role === "admin" ? "Administrador" : "Analista"}</Badge>
            </Group>
            <Text size="sm">Utilizador: <b>{user.username}</b></Text>
            <Text size="sm">Último login: {formatDate(user.last_login_at)}</Text>
            <Text size="sm">Password alterada: {formatDate(user.password_changed_at)}</Text>
            <Text size="sm">Verificação em dois passos: <b>ativa</b> · códigos de recuperação por usar:{" "}
              <b>{user.recovery_codes_left}</b></Text>
            <Group>
              <Button size="xs" variant="light" onClick={() => { setCodes(null); setCodesOpen(true); }}>
                Gerar novos códigos de recuperação
              </Button>
            </Group>
            <Text size="xs" c="dimmed">Mudou de telemóvel? Peça a um administrador para repor o MFA; volta a
              configurá-lo no próximo login.</Text>
          </Stack>
        </Card>
        <Card withBorder>
          <form onSubmit={changePassword}>
            <Stack gap="xs">
              <Text fw={600}>Mudar password</Text>
              <PasswordInput label="Password atual" autoComplete="current-password" required value={current}
                onChange={(e) => setCurrent(e.currentTarget.value)} />
              <PasswordFields value={next} onChange={setNext} confirm={confirm} onConfirm={setConfirm} />
              <Group><Button type="submit" loading={busy} disabled={!next || next !== confirm}>Mudar password</Button>
              </Group>
            </Stack>
          </form>
        </Card>
      </SimpleGrid>

      <Card withBorder>
        <Group justify="space-between" mb="xs">
          <Text fw={600}>Sessões ativas</Text>
          <Button size="xs" variant="light" color="red" disabled={sessions.length < 2} onClick={async () => {
            try {
              await authApi.endOtherSessions();
              loadSessions();
            } catch (err) {
              notifyError(err);
            }
          }}>Terminar as outras sessões</Button>
        </Group>
        <Table.ScrollContainer minWidth={600}>
          <Table>
            <Table.Thead>
              <Table.Tr><Table.Th>Início</Table.Th><Table.Th>Última atividade</Table.Th><Table.Th>IP</Table.Th>
                <Table.Th>Navegador</Table.Th><Table.Th /></Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {sessions.map((s) => (
                <Table.Tr key={s.id}>
                  <Table.Td>{formatDate(s.created_at)}</Table.Td>
                  <Table.Td>{formatDate(s.last_seen_at)}</Table.Td>
                  <Table.Td>{s.ip || "—"}</Table.Td>
                  <Table.Td><Text size="xs" lineClamp={1} title={s.user_agent}>{s.user_agent}</Text></Table.Td>
                  <Table.Td>
                    {s.current ? <Badge size="sm">esta</Badge> : (
                      <Button size="compact-xs" variant="subtle" color="red" onClick={async () => {
                        try {
                          await authApi.endSession(s.id);
                          loadSessions();
                        } catch (err) {
                          notifyError(err);
                        }
                      }}>Terminar</Button>
                    )}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Card>

      <Modal opened={codesOpen} onClose={() => { setCodesOpen(false); void refresh(); }} withCloseButton={!codes}
        closeOnClickOutside={!codes} closeOnEscape={!codes} title="Novos códigos de recuperação" size="md">
        {codes ? (
          <RecoveryCodesBody codes={codes} onDone={() => { setCodesOpen(false); void refresh(); }} />
        ) : (
          <Stack>
            <Alert color="yellow">Os códigos anteriores deixam de funcionar.</Alert>
            <PasswordInput label="Confirme com a sua password" autoComplete="current-password" value={codesPassword}
              onChange={(e) => setCodesPassword(e.currentTarget.value)} />
            <Button disabled={!codesPassword} onClick={regenerate}>Gerar</Button>
          </Stack>
        )}
      </Modal>
    </Stack>
  );
}
