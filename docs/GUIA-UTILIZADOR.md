# Guia do utilizador

Este guia segue o trabalho num processo, do início ao relatório. Detalhes de cada funcionalidade: [TECNICO.md](TECNICO.md).

> Os resultados de reconhecimento facial são **indicativos**. Não constituem identificação conclusiva e têm de ser validados por um perito.

## 0. Entrar

- Abra o endereço do FaceAI (por omissão `http://localhost:8000`) e entre com a sua conta.
- As contas são criadas pelo administrador, em *Utilizadores*. Na primeira entrada pode ser pedido para mudar a password ou configurar o MFA.
- Em **A minha conta** muda a password, ativa o MFA (app autenticadora) e vê as sessões abertas.

## 1. Criar o processo

- Em **Processos → Novo processo**, dê um nome (por exemplo, o n.º do inquérito) e, se quiser, uma descrição.
- Quem cria o processo fica **responsável**. No menu **⋯** junto ao nome, **Equipa** dá acesso a outros utilizadores, como membro ou como responsável.
- O separador **Resumo** mostra os **primeiros passos**. Cada passo abre o separador certo e fica marcado quando está feito.

## 2. Pessoas (fotos da pessoa a procurar)

- Em **1 · Pessoas**, arraste fotos da pessoa. Cada foto deve ter uma só face visível. Fotos de ângulos e luz diferentes ajudam.
- Crie uma **pessoa** (*Nova pessoa*) para juntar as fotos de cada suspeito. Podem procurar-se várias pessoas na mesma análise.
- Clique numa foto para a ver em tamanho grande (← / → para passar às outras).
- **Não tem fotos?** Passe à frente e use a análise **Descobrir pessoas**: todas as faces são agrupadas em pessoas distintas, e qualquer uma pode depois servir de foto.

## 3. Vídeos

- Em **2 · Vídeos**, crie um **local** por estabelecimento ou zona (por exemplo, "Café Central") e importe os vídeos de cada câmara.
- **Importar pastas** importa uma pasta inteira: cada subpasta passa a ser um local.
- Na importação é calculado o **SHA-256** de cada ficheiro, e os duplicados são recusados.
- Os ficheiros `.sec` são preparados automaticamente. Os formatos que o browser não abre (por exemplo, H.265) ficam "Ao abrir": quando abre o vídeo, é criada uma cópia leve só para visualização, que é apagada sozinha se não for usada durante algum tempo. A análise usa sempre o original.
- **Acertar hora** (só o responsável): lê a data/hora gravada na imagem (OCR), ou acerta-a à mão, para os resultados virem em **hora real**.
- **Proporção da imagem:** alguns DVR gravam com píxeis que não são quadrados (modos "Lite"). Fora do leitor do fabricante, a imagem fica quase quadrada e deformada. O FaceAI ajusta a proporção e os frames automaticamente, e o tamanho mostrado aparece junto ao vídeo (por exemplo, 1920×1080). Se a imagem continuar deformada, o responsável altera-a no menu **⋯** do vídeo → **Proporção da imagem…**. O ficheiro não muda, e as análises novas veem as caras com a forma real.
- **Detalhes técnicos** mostra o tamanho, fps, resolução, codec e o início do SHA-256 de cada vídeo.

## 4. Análise

Em **3 · Análises → Nova análise**:

1. **Modo:**
   - *Procurar pessoa (com fotos)*;
   - *Descobrir pessoas (sem fotos)*.
2. **Locais:** por omissão entram todos. Em *Ajustar vídeos* pode retirar câmaras.
3. **Pessoas** a procurar.
4. **Rigor:**
   - *Rápida*: uma primeira passagem por muitas horas;
   - *Equilibrada*: recomendada;
   - *Minuciosa*: mais frames e faces mais pequenas, mais lenta.
5. Se fizer sentido, **Procurar também pela roupa**.
6. **Iniciar análise.** Se o botão estiver desativado, o motivo aparece ao lado.

Pode sair da página: a análise continua no servidor e o progresso aparece na barra "Em curso". As **Opções avançadas** só são precisas em casos especiais. O perfil escolhido fica registado no relatório.

## 5. Rever os resultados

Ao abrir uma análise, a faixa no topo diz **quantas aparições** há e quantas faltam rever.

- **Rever pendentes** mostra só o que falta rever.
- **Rever cada aparição:**
  - **✓ confirmar** ou **✗ rejeitar** a aparição inteira (todas as faces dessa passagem);
  - as rejeitadas ficam ocultas, e **Anular** desfaz logo a seguir;
  - **🔍** abre o frame completo: com a marcação, o original (PNG) ou uma versão melhorada só para visualização.
- **Ver as faces:** *Ver faces* mostra as faces individuais de uma aparição.
- **Filtros:** semelhança mínima, candidatos abaixo do limiar, rejeitadas e ordenação.
- **Guardar frame:** no leitor, guarda o frame original do instante atual como imagem validada.
- **Modo descoberta:**
  - a vista **Pessoas** agrupa as faces de todos os vídeos e locais da análise; o emblema **N locais** mostra quem aparece em vários;
  - **Alertas automáticos:** pessoas desconhecidas vistas em vários locais e pessoas parecidas com as de outras análises do processo ("pode ser a Pessoa #2 da análise #5", com **Abrir análise #5**). São indícios para confirmar ou descartar;
  - pode juntar duas pessoas que sejam a mesma;
  - pode usar uma face como foto para procurar essa pessoa noutros vídeos.

## 6. Resultados do processo e relatório

- **4 · Resultados → Imagens validadas:** frames das aparições confirmadas e frames guardados, por local, com download em ZIP (frames originais, manifesto e SHA-256).
- **4 · Resultados → Percurso:** junta as aparições de várias análises pela hora real, para ver o percurso da pessoa entre câmaras.
- **Exportar relatório** (na análise): PDF com os parâmetros, os hashes, a cadeia de auditoria e as aparições confirmadas. O menu *Exportar* tem também HTML, CSV, JSON e um pacote ZIP completo.

## Quem pode fazer o quê

| | Membro | Responsável / administrador |
|---|:-:|:-:|
| Importar fotos e vídeos, iniciar análises | ✓ | ✓ |
| Rever, juntar pessoas, guardar frames, exportar | ✓ | ✓ |
| Acertar o relógio, corrigir fps | | ✓ |
| Eliminar fotos, pessoas, locais, vídeos, análises | | ✓ |
| Equipa, editar e eliminar o processo, libertar armazenamento | | ✓ |

Tudo o que altera dados fica na **auditoria** (menu **⋯ → Auditoria**), numa cadeia de hashes que deteta alterações.
