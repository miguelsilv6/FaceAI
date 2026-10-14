import {
  Alert, Badge, Button, Card, Group, Modal, PasswordInput, SimpleGrid, Stack, Table, Text, TextInput, Title,
} from "@mantine/core";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";

import { authApi, formatDate, setCsrfToken } from "../api";
import { useAuth } from "../auth";
import { notifyError, notifyOk } from "../notify";
import type { MfaSetup, UserSessionInfo } from "../types";
import { CodeForm, MfaQr, PasswordFields, RecoveryCodesBody } from "./Login";

export default function Account() {
  const { user, refresh, requireMfa } = useAuth();
  const [sessions, setSessions] = useState<UserSessionInfo[]>([]);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [codesPassword, setCodesPassword] = useState("");
  const [codesOpen, setCodesOpen] = useState(false);
  const [codes, setCodes] = useState<string[] | null>(null);
  const [mfaOn, setMfaOn] = useState(false);
  const [mfaOff, setMfaOff] = useState(false);

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
            {user.totp_enabled ? (
              <>
                <Text size="sm">Verificação em dois passos: <Badge color="teal" variant="light">ativa</Badge>{" "}
                  · códigos de recuperação por usar: <b>{user.recovery_codes_left}</b></Text>
                <Group>
                  <Button size="xs" variant="light" onClick={() => { setCodes(null); setCodesOpen(true); }}>
                    Gerar novos códigos de recuperação
                  </Button>
                  {!requireMfa && <Button size="xs" variant="subtle" color="red" onClick={() => setMfaOff(true)}>
                    Desativar</Button>}
                </Group>
                <Text size="xs" c="dimmed">Mudou de telemóvel? Entre com um código de recuperação, desative e volte a
                  ativar o MFA, ou peça a um administrador para o repor.</Text>
              </>
            ) : (
              <>
                <Text size="sm">Verificação em dois passos: <Badge color="gray" variant="light">desativada</Badge></Text>
                <Text size="xs" c="dimmed">Recomendado: além da password, o login passa a pedir um código da app
                  autenticadora do telemóvel. Protege a conta se a password for descoberta.</Text>
                <Group><Button size="xs" onClick={() => setMfaOn(true)}>Ativar verificação em dois passos</Button></Group>
              </>
            )}
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

      <Modal opened={mfaOn} onClose={() => { setMfaOn(false); void refresh(); }} closeOnClickOutside={false}
        title="Ativar verificação em dois passos">
        {mfaOn && <EnableMfa username={user.username} onDone={() => { setMfaOn(false); void refresh(); }} />}
      </Modal>
      <Modal opened={mfaOff} onClose={() => setMfaOff(false)} title="Desativar verificação em dois passos">
        {mfaOff && <DisableMfa onDone={async () => {
          setMfaOff(false);
          notifyOk("Verificação em dois passos desativada. As outras sessões foram terminadas.");
          await refresh();
          loadSessions();
        }} />}
      </Modal>
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

function EnableMfa({ username, onDone }: { username: string; onDone: () => void }) {
  const [setup, setSetup] = useState<MfaSetup | null>(null);
  const [codes, setCodes] = useState<string[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);
  useEffect(() => {
    // Uma só vez: cada pedido gera um segredo novo e invalida o QR anterior.
    if (started.current) return;
    started.current = true;
    authApi.mfaSetup().then(setSetup).catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);
  if (codes) return <RecoveryCodesBody codes={codes} onDone={onDone} />;
  const submit = async (code: string) => {
    setBusy(true);
    setError(null);
    try {
      setCodes((await authApi.mfaEnable(code)).recovery_codes);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Stack>
      {setup && <MfaQr setup={setup} />}
      <CodeForm label={`Código de 6 dígitos (conta ${username})`} allowRecovery={false} busy={busy} error={error}
        onSubmit={(code) => void submit(code)} />
    </Stack>
  );
}

function DisableMfa({ onDone }: { onDone: () => void }) {
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await authApi.mfaDisable(password, code);
      onDone();
    } catch (err) {
      notifyError(err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form onSubmit={submit}>
      <Stack>
        <Alert color="yellow">Sem o segundo fator, basta a password para entrar na conta.</Alert>
        <PasswordInput label="Password" autoComplete="current-password" required value={password}
          onChange={(e) => setPassword(e.currentTarget.value)} />
        <TextInput label="Código da app (ou de recuperação)" autoComplete="one-time-code" required value={code}
          onChange={(e) => setCode(e.currentTarget.value)} />
        <Button type="submit" color="red" loading={busy}>Desativar</Button>
      </Stack>
    </form>
  );
}
