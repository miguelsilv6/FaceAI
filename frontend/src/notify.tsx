import { Button } from "@mantine/core";
import { notifications } from "@mantine/notifications";

export const notifyError = (err: unknown, title = "Erro") =>
  notifications.show({ color: "red", title, message: err instanceof Error ? err.message : String(err) });

export const notifyOk = (message: string, title = "Concluído") =>
  notifications.show({ color: "green", title, message });

/** Notificação com um botão para desfazer a ação acabada de fazer. */
export const notifyUndo = (message: string, onUndo: () => void) => {
  const id = notifications.show({
    color: "gray", title: "Oculta da lista", autoClose: 6000,
    message: (
      <span>{message}{" "}
        <Button size="compact-xs" variant="light" onClick={() => { notifications.hide(id); onUndo(); }}>Anular</Button>
      </span>
    ),
  });
};
