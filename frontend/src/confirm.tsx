import { Button, Group, Modal, Stack, Text } from "@mantine/core";
import { useEffect, useState } from "react";

export interface ConfirmOptions {
  title: string;
  message: string;
  confirmLabel?: string;
  danger?: boolean;
}

type Pending = ConfirmOptions & { resolve: (ok: boolean) => void };
let show: ((p: Pending) => void) | null = null;

/** Pede confirmação numa janela da aplicação (em vez do window.confirm do browser). */
export function confirmAction(opts: ConfirmOptions): Promise<boolean> {
  return new Promise((resolve) => {
    if (show) show({ ...opts, resolve });
    else resolve(window.confirm(`${opts.title}\n\n${opts.message}`));
  });
}

/** Montado uma vez na raiz da aplicação. */
export function ConfirmHost() {
  const [pending, setPending] = useState<Pending | null>(null);
  useEffect(() => {
    show = setPending;
    return () => { show = null; };
  }, []);
  const close = (ok: boolean) => {
    pending?.resolve(ok);
    setPending(null);
  };
  return (
    <Modal opened={!!pending} onClose={() => close(false)} title={pending?.title} centered>
      <Stack>
        <Text size="sm" style={{ whiteSpace: "pre-line" }}>{pending?.message}</Text>
        <Group justify="flex-end">
          <Button variant="default" onClick={() => close(false)}>Cancelar</Button>
          <Button color={pending?.danger ? "red" : undefined} onClick={() => close(true)} data-autofocus>
            {pending?.confirmLabel ?? "Confirmar"}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
