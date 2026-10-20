import {
  Alert, Anchor, Button, Center, Checkbox, Code, CopyButton, Group, Image, Paper, PasswordInput, PinInput, SimpleGrid,
  Stack, Text, TextInput, Title,
} from "@mantine/core";
import { IconAlertTriangle, IconLock, IconScan, IconShieldLock } from "@tabler/icons-react";
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";

import { authApi } from "../api";
import Signature from "../components/Signature";
import type { AuthState, MfaSetup } from "../types";

interface Props {
  state: AuthState | null;
  setupRequired: boolean;
  expired: boolean;
  onState: (s: AuthState) => void;
  onCancel: () => void;
}

function Frame({ title, icon, children }: { title: string; icon?: ReactNode; children: ReactNode }) {
  return (
    <Center mih="100vh" p="md" style={{ flexDirection: "column" }}>
      <Paper withBorder shadow="sm" p="xl" w="100%" maw={440}>
        <Stack>
          <Group gap="xs">
            {icon ?? <IconScan size={28} />}
            <Title order={3}>{title}</Title>
          </Group>
          {children}
        </Stack>
      </Paper>
      <Signature />
    </Center>
  );
}

function useSubmit() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, run };
}

export default function LoginFlow({ state, setupRequired, expired, onState, onCancel }: Props) {
  // Códigos de recuperação mostrados uma única vez, logo a seguir à configuração do MFA.
  const [pending, setPending] = useState<AuthState | null>(null);
  if (pending?.recovery_codes?.length) {
    return <RecoveryCodes codes={pending.recovery_codes} onDone={() => {
      const next = { ...pending, recovery_codes: [] };
      setPending(null);
      onState(next);
    }} />;
  }
  if (setupRequired && !state) return <SetupStep onState={onState} />;
  if (!state) return <PasswordStep expired={expired} onState={onState} />;
  if (state.stage === "mfa_setup") return <MfaSetupStep onState={setPending} onCancel={onCancel} />;
  if (state.stage === "mfa") return <MfaStep onState={onState} onCancel={onCancel} />;
  return <ForcedPasswordChange state={state} onState={onState} onCancel={onCancel} />;
}

/** Primeiro arranque: cria o administrador no browser com o código de configuração do servidor. */
function SetupStep({ onState }: { onState: (s: AuthState) => void }) {
  const [code, setCode] = useState("");
  const [username, setUsername] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const { busy, error, run } = useSubmit();
  const mismatch = repeat.length > 0 && repeat !== password;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (mismatch) return;
    void run(async () => {
      await authApi.setup({ code, username, display_name: name, password });
      onState(await authApi.login(username, password));
    });
  };
  return (
    <Frame title="Configuração inicial" icon={<IconShieldLock size={28} />}>
      <Text size="sm">Ainda não existe nenhum administrador. Crie-o aqui.</Text>
      <Text size="sm" c="dimmed">
        O <b>código de configuração</b> está no ficheiro <Code>setup-code.txt</Code> da pasta de dados (com Docker:
        {" "}<Code>./data/setup-code.txt</Code>) e nos registos do servidor. Só quem tem acesso ao servidor o vê; deixa de
        valer depois de criado o administrador.
      </Text>
      <form onSubmit={submit}>
        <Stack>
          <TextInput label="Código de configuração" placeholder="XXXXXX-XXXXXX-XXXXXX-XXXXXX" required autoFocus
            autoComplete="off" value={code} onChange={(e) => setCode(e.currentTarget.value)} />
          <TextInput label="Utilizador" description="3–32 caracteres: letras minúsculas, dígitos, '.', '_' ou '-'"
            autoComplete="username" required value={username} onChange={(e) => setUsername(e.currentTarget.value)} />
          <TextInput label="Nome" value={name} onChange={(e) => setName(e.currentTarget.value)} />
          <PasswordInput label="Password" autoComplete="new-password" required value={password}
            onChange={(e) => setPassword(e.currentTarget.value)} />
          <PasswordInput label="Repetir a password" autoComplete="new-password" required value={repeat}
            error={mismatch ? "As passwords não coincidem" : undefined} onChange={(e) => setRepeat(e.currentTarget.value)} />
          {error && <Alert color="red" icon={<IconAlertTriangle size={16} />}>{error}</Alert>}
          <Button type="submit" loading={busy} disabled={mismatch}>Criar administrador e entrar</Button>
          <Text size="xs" c="dimmed">Alternativa na consola: <Code>python -m app.cli create-admin</Code>.</Text>
        </Stack>
      </form>
    </Frame>
  );
}

function PasswordStep({ expired, onState }: { expired: boolean; onState: (s: AuthState) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const { busy, error, run } = useSubmit();
  const submit = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => onState(await authApi.login(username, password)));
  };
  return (
    <Frame title="FaceAI — iniciar sessão">
      {expired && <Alert color="yellow">A sessão terminou (inatividade ou fim de sessão). Inicie sessão de novo.</Alert>}
      <form onSubmit={submit}>
        <Stack>
          <TextInput label="Utilizador" autoComplete="username" required autoFocus value={username}
            onChange={(e) => setUsername(e.currentTarget.value)} />
          <PasswordInput label="Password" autoComplete="current-password" required value={password}
            onChange={(e) => setPassword(e.currentTarget.value)} />
          {error && <Alert color="red" icon={<IconAlertTriangle size={16} />}>{error}</Alert>}
          <Button type="submit" loading={busy} leftSection={<IconLock size={16} />}>Entrar</Button>
        </Stack>
      </form>
    </Frame>
  );
}

export function CodeForm({ label, allowRecovery, onSubmit, busy, error }: {
  label: string; allowRecovery: boolean; onSubmit: (code: string) => void; busy: boolean; error: string | null;
}) {
  const [code, setCode] = useState("");
  const [recovery, setRecovery] = useState(false);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    onSubmit(code);
  };
  return (
    <form onSubmit={submit}>
      <Stack>
        {recovery ? (
          <TextInput label="Código de recuperação" placeholder="xxxx-xxxx" autoComplete="off" required autoFocus
            value={code} onChange={(e) => setCode(e.currentTarget.value)} />
        ) : (
          <Stack gap={4}>
            <Text size="sm" fw={500}>{label}</Text>
            <PinInput length={6} type="number" oneTimeCode autoFocus value={code} onChange={setCode}
              aria-label="Código de 6 dígitos" />
          </Stack>
        )}
        {error && <Alert color="red" icon={<IconAlertTriangle size={16} />}>{error}</Alert>}
        <Button type="submit" loading={busy} disabled={!recovery && code.length !== 6}>Confirmar</Button>
        {allowRecovery && (
          <Anchor component="button" type="button" size="sm" onClick={() => { setRecovery(!recovery); setCode(""); }}>
            {recovery ? "Usar a app autenticadora" : "Não tenho o telemóvel: usar um código de recuperação"}
          </Anchor>
        )}
      </Stack>
    </form>
  );
}

function MfaStep({ onState, onCancel }: { onState: (s: AuthState) => void; onCancel: () => void }) {
  const { busy, error, run } = useSubmit();
  return (
    <Frame title="Verificação em dois passos" icon={<IconShieldLock size={28} />}>
      <CodeForm label="Código da app autenticadora" allowRecovery busy={busy} error={error}
        onSubmit={(code) => void run(async () => onState(await authApi.mfaVerify(code)))} />
      <Anchor component="button" type="button" size="sm" c="dimmed" onClick={onCancel}>Cancelar</Anchor>
    </Frame>
  );
}

function MfaSetupStep({ onState, onCancel }: { onState: (s: AuthState) => void; onCancel: () => void }) {
  const [setup, setSetup] = useState<MfaSetup | null>(null);
  const { busy, error, run } = useSubmit();
  const started = useRef(false);
  useEffect(() => {
    // Uma só vez (cada pedido gera um segredo novo e invalida o QR anterior).
    if (started.current) return;
    started.current = true;
    void run(async () => setSetup(await authApi.mfaSetup()));
  }, [run]);
  return (
    <Frame title="Configurar verificação em dois passos" icon={<IconShieldLock size={28} />}>
      <Text size="sm">Esta instalação exige um segundo fator.</Text>
      {setup && <MfaQr setup={setup} />}
      <CodeForm label="Código de 6 dígitos" allowRecovery={false} busy={busy} error={error}
        onSubmit={(code) => void run(async () => onState(await authApi.mfaVerify(code)))} />
      <Anchor component="button" type="button" size="sm" c="dimmed" onClick={onCancel}>Cancelar</Anchor>
    </Frame>
  );
}

export function MfaQr({ setup }: { setup: MfaSetup }) {
  return (
    <>
      <Text size="sm">Leia o código QR com uma app autenticadora (por exemplo Microsoft Authenticator, Google
        Authenticator, Aegis ou 2FAS) e escreva o código de 6 dígitos que aparece.</Text>
      <Center><Image src={setup.qr_svg} alt="Código QR para a app autenticadora" w={220} h={220} /></Center>
      <Text size="xs" c="dimmed" ta="center">Sem câmara? Introduza a chave manualmente:</Text>
      <Group justify="center" gap="xs">
        <Code style={{ wordBreak: "break-all" }}>{setup.secret.match(/.{1,4}/g)?.join(" ")}</Code>
        <CopyButton value={setup.secret}>
          {({ copied, copy }) => <Button size="compact-xs" variant="light" onClick={copy}>
            {copied ? "Copiada" : "Copiar"}</Button>}
        </CopyButton>
      </Group>
    </>
  );
}

function RecoveryCodes({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  return (
    <Frame title="Códigos de recuperação" icon={<IconShieldLock size={28} />}>
      <RecoveryCodesBody codes={codes} onDone={onDone} />
    </Frame>
  );
}

export function RecoveryCodesBody({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  const [saved, setSaved] = useState(false);
  const text = codes.join("\n");
  const download = () => {
    const url = URL.createObjectURL(new Blob([`FaceAI — códigos de recuperação\n\n${text}\n`], { type: "text/plain" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "faceai-codigos-recuperacao.txt";
    a.click();
    URL.revokeObjectURL(url);
  };
  return (
    <Stack>
      <Text size="sm">Se perder o acesso à app autenticadora, cada um destes códigos permite entrar <b>uma vez</b>.
        Guarde-os num local seguro (ex.: gestor de passwords). Não voltam a ser mostrados.</Text>
      <SimpleGrid cols={2} spacing="xs">
        {codes.map((c) => <Code key={c} ta="center" fz="md">{c}</Code>)}
      </SimpleGrid>
      <Group grow>
        <CopyButton value={text}>
          {({ copied, copy }) => <Button variant="light" onClick={copy}>{copied ? "Copiados" : "Copiar"}</Button>}
        </CopyButton>
        <Button variant="light" onClick={download}>Descarregar .txt</Button>
      </Group>
      <Checkbox label="Guardei os códigos num local seguro" checked={saved}
        onChange={(e) => setSaved(e.currentTarget.checked)} />
      <Button disabled={!saved} onClick={onDone}>Continuar</Button>
    </Stack>
  );
}

export function PasswordFields({ value, onChange, confirm, onConfirm }: {
  value: string; onChange: (v: string) => void; confirm: string; onConfirm: (v: string) => void;
}) {
  return (
    <>
      <PasswordInput label="Nova password" description="Pelo menos 12 caracteres. Uma frase com várias palavras é
        mais fácil de lembrar e mais forte." autoComplete="new-password" required value={value}
        onChange={(e) => onChange(e.currentTarget.value)} />
      <PasswordInput label="Repetir a nova password" autoComplete="new-password" required value={confirm}
        error={confirm && confirm !== value ? "As passwords não coincidem." : undefined}
        onChange={(e) => onConfirm(e.currentTarget.value)} />
    </>
  );
}

function ForcedPasswordChange({ state, onState, onCancel }: {
  state: AuthState; onState: (s: AuthState) => void; onCancel: () => void;
}) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const { busy, error, run } = useSubmit();
  const submit = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => onState(await authApi.changePassword(current, next)));
  };
  return (
    <Frame title="Definir uma password nova" icon={<IconLock size={28} />}>
      <Text size="sm">Olá {state.user.display_name || state.user.username}. A password atual é temporária (definida
        por um administrador): escolha uma nova para continuar.</Text>
      <form onSubmit={submit}>
        <Stack>
          <PasswordInput label="Password temporária" autoComplete="current-password" required value={current}
            onChange={(e) => setCurrent(e.currentTarget.value)} />
          <PasswordFields value={next} onChange={setNext} confirm={confirm} onConfirm={setConfirm} />
          {error && <Alert color="red" icon={<IconAlertTriangle size={16} />}>{error}</Alert>}
          <Button type="submit" loading={busy} disabled={!next || next !== confirm}>Guardar e continuar</Button>
          <Anchor component="button" type="button" size="sm" c="dimmed" onClick={onCancel}>Cancelar</Anchor>
        </Stack>
      </form>
    </Frame>
  );
}
