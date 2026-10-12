import { notifications } from "@mantine/notifications";

export const notifyError = (err: unknown, title = "Erro") =>
  notifications.show({ color: "red", title, message: err instanceof Error ? err.message : String(err) });

export const notifyOk = (message: string, title = "Concluído") =>
  notifications.show({ color: "green", title, message });
