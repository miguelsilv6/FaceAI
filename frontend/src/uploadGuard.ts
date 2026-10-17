import { useEffect, useState } from "react";

import { onUploadsChange, uploadsActive } from "./api";

/**
 * Enquanto houver uploads em curso, fechar o separador, recarregar ou sair para outro site pede confirmação ao
 * browser (que mostra a sua própria mensagem; o texto não pode ser personalizado). Navegar dentro da aplicação não
 * interrompe os uploads: continuam em segundo plano e aparecem na barra "Em curso".
 */
function guard(e: BeforeUnloadEvent) {
  if (!uploadsActive()) return;
  e.preventDefault();
  e.returnValue = ""; // browsers antigos
}

let installed = false;
export function installUploadGuard() {
  if (installed) return;
  installed = true;
  window.addEventListener("beforeunload", guard);
}

/** N.º de uploads em curso neste separador (atualiza o componente). */
export function useActiveUploads(): number {
  const [n, setN] = useState(uploadsActive());
  useEffect(() => onUploadsChange(setN), []);
  return n;
}

export const LEAVE_WARNING = "Há uploads de vídeos em curso. Se sair, os uploads são interrompidos (pode retomá-los "
  + "mais tarde, largando os mesmos ficheiros no mesmo local). Sair mesmo assim?";
