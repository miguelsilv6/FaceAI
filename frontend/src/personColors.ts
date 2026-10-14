/** Cor (Mantine) de cada pessoa procurada, estável pelo n.º da pessoa. */
const COLORS = ["grape", "teal", "orange", "cyan", "pink", "lime", "violet", "yellow"];

export function personColor(personId: number | null | undefined): string {
  return personId == null ? "gray" : COLORS[(personId - 1) % COLORS.length];
}
