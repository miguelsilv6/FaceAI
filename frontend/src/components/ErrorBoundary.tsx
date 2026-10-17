import { Alert, Button, Code, Group, Stack, Text } from "@mantine/core";
import { IconAlertTriangle } from "@tabler/icons-react";
import { Component, type ErrorInfo, type ReactNode } from "react";

interface State {
  error: Error | null;
}

/** Erro de render numa página: mostra-o em vez de deixar o ecrã em branco. */
export default class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <Alert color="red" icon={<IconAlertTriangle size={18} />} title="Ocorreu um erro nesta página" m="md">
        <Stack gap="xs">
          <Text size="sm">Os dados não foram afetados. Recarregue a página; se o erro se repetir, comunique a mensagem abaixo.</Text>
          <Code block>{error.message || String(error)}</Code>
          <Group gap="xs">
            <Button size="xs" onClick={() => window.location.reload()}>Recarregar</Button>
            <Button size="xs" variant="default" component="a" href="/">Voltar aos processos</Button>
          </Group>
        </Stack>
      </Alert>
    );
  }
}
