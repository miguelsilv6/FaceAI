# FaceAI — deteção de uma pessoa em vídeos de CCTV

Aplicação web local para apoio à investigação forense digital. A partir de uma ou mais **imagens de referência** da face de uma pessoa, analisa **vídeos de CCTV** importados e indica **se** essa pessoa aparece, **quando** (timestamps e intervalos) e **com que grau de semelhança**.

Inclui ainda:
- **modo descoberta**, para quando não há amostra: todas as faces são detetadas e agrupadas em **pessoas distintas**, e qualquer uma pode depois servir de amostra;
- tracking de faces e revisão humana dos resultados (por pessoa, por track ou por deteção);
- **download do frame** de cada deteção, com marcação (JPEG) ou **original sem marcação** (PNG extraído do vídeo), registado na auditoria com SHA-256;
- **hora real** do CCTV, por OCR da data/hora gravada na imagem, acerto manual e desvio do relógio do DVR;
- score de qualidade de cada face;
- relatório PDF/HTML e exportação CSV/JSON/ZIP;
- log de auditoria;
- upload em blocos com retoma.

> ⚠️ Os resultados de reconhecimento facial são **indicativos**. Não constituem identificação conclusiva e têm de ser validados por um perito.

---

## Soluções open source usadas (e alternativas)

| Camada | Escolha | Alternativas consideradas |
|---|---|---|
| Runtime de inferência | **ONNX Runtime** (CPU, ou GPU NVIDIA com `onnxruntime-gpu`) | TensorFlow (motor DeepFace, opcional), PyTorch |
| Deteção facial | **SCRFD-10G** (InsightFace), com **SCRFD-500M** como 1.ª passagem rápida | RetinaFace, YuNet, MTCNN, YOLOv8-face |
| Reconhecimento (embeddings) | **ArcFace ResNet-50 / MobileFaceNet**, treinados no WebFace600K (InsightFace `buffalo_l` / `buffalo_sc`) | Facenet512, VGG-Face (via DeepFace) |
| Motor alternativo | **[DeepFace](https://github.com/serengil/deepface)** (MIT), com `FACEAI_ENGINE=deepface` | CompreFace, facenet-pytorch |
| Vídeo | **OpenCV** (amostragem, pré-filtro de movimento) + **ffmpeg** (cópia de reprodução) | PyAV, decord |
| Comparação / tracking | Distância de cosseno em NumPy; tracker IoU + embedding | FAISS; ByteTrack/DeepSORT |
| Backend | **FastAPI** + SQLModel/SQLite | Flask, Django |
| Frontend | **React + Vite + Mantine** | Gradio/Streamlit, PySide6 |
| Relatórios | **Jinja2 + WeasyPrint** | ReportLab |

---

## Como funciona

```
Referências ──► SCRFD ──► alinhamento (5 pontos) ──► ArcFace (512-d) ─────────────────────────┐
                                                                                              ▼
Vídeo ─► frames a N fps ─► [1] filtro de movimento ─► [2] SCRFD-500M rápido ─► [3] SCRFD-10G + ArcFace
                             (saltar se estático)       (saltar se sem faces)            │
                                                                                         ▼
                 [4] qualidade da face ─► [5] tracking ─► distância de cosseno às referências
                                                                                         │
           segmentos de aparição ◄── tracks / deteções com match ◄── revisão humana ◄────┘
```

1. **Referências.** Cada imagem tem de conter **exatamente uma face**. As imagens com 0 ou mais de 1 face ficam marcadas como inválidas. Ter várias imagens (ângulos, iluminação, idades diferentes) aumenta a sensibilidade.
2. **Pré-filtro de movimento.** Cada frame amostrado é comparado, numa versão pequena e desfocada, com o último frame analisado. Se a imagem não mudou (por omissão, menos de 0,2 % dos píxeis), o frame é saltado. Um *frame-chave* periódico (5 s) garante que cenas paradas continuam a ser verificadas. Quando um frame é saltado por ser idêntico, as faces do último frame analisado continuam presentes, por isso o track de alguém parado mantém a duração correta.
3. **Cascata de deteção.** O SCRFD-500M (~13 ms/frame) verifica primeiro se há faces. O detetor principal e o reconhecimento só correm nos frames com faces.
4. **Comparação.** Cada face é alinhada com 5 pontos, convertida num embedding ArcFace e comparada com todas as referências; conta a menor distância de cosseno. Há **match** quando `distância ≤ limiar`. O limiar por omissão é 0,65, calibrado com 300 pares rotulados: mesma pessoa até 0,45; pessoas diferentes a partir de 0,78.
5. **Qualidade da face (0–100).** É a média geométrica de três componentes: **nitidez** (variância do Laplaciano), **tamanho** e **frontalidade** (posição do nariz face aos olhos). Serve para ordenar e filtrar deteções, e para explicar porque um match é fraco.
6. **Tracking.** As deteções da mesma face em frames consecutivos são agrupadas num *track*, por semelhança do embedding e sobreposição das caixas. O match do track decide-se pela **média das 3 melhores distâncias**, mais robusta do que frames isolados. Depois de um track ter uma correspondência, os frames seguintes da mesma face também são guardados, mesmo os de perfil ou desfocados, o que dá ao perito a trajetória completa. **Na revisão, um track inteiro confirma-se ou rejeita-se de uma vez.**
7. **Hora real.** Ver a secção [Hora real do CCTV](#hora-real-do-cctv).
8. **Segmentos e prova.** Os tracks e as deteções com match formam os intervalos de aparição. Por cada deteção ficam guardados: recorte da face, frame anotado, timestamp, nº de frame, caixa, confiança, distância, qualidade e track.

### Desempenho (CPU, 4 vCPU)

| | DeepFace (versão anterior) | ONNX (atual) |
|---|---|---|
| Deteção + reconhecimento, por frame | ~1,6 s | ~0,07 s de deteção + ~0,08 s por face (R50); ~0,01 s por face com MobileFaceNet |
| Vídeo de teste de 20 s, 5 fps, 3 pessoas | — | 28,5 s sem filtros → **3,6 s** com movimento + cascata + tracking, com os **mesmos segmentos** |
| RAM | ~1,4 GB | ~0,56 GB |
| Instalação | ~2,8 GB (TensorFlow) | ~0,5 GB + 198 MB de modelos |

Em CCTV real, o ganho do pré-filtro depende do movimento na cena: quanto mais estática a câmara, maior.

---

## Instalação

📄 **Guia completo:** [`docs/INSTALACAO.md`](docs/INSTALACAO.md). Cobre os requisitos da VM, a instalação nativa, Docker, GPU, posto offline, systemd, Cloudflare Tunnel e a resolução de problemas.

### Resumo (desenvolvimento)

Requisitos: Python 3.11+, Node 20+, `ffmpeg` (recomendado) e as bibliotecas do WeasyPrint (Pango).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e "backend[cpu,dev]"        # GPU NVIDIA: "backend[gpu,dev]"; motor DeepFace: acrescentar ",deepface"
cd backend && uvicorn app.main:app --reload --port 8000

cd frontend && npm install && npm run dev   # http://localhost:5173 (encaminha /api para :8000)
```

Para usar só o backend a servir a interface, corre `npm run build` em `frontend/` e abre `http://localhost:8000`.

Na primeira utilização, os pesos ONNX (~198 MB) são descarregados dos *releases* do InsightFace para `~/.faceai/models` (ou para `$FACEAI_MODELS_DIR`). Cada ficheiro é **verificado por SHA-256**, e os hashes ficam fixados no código e registados no relatório. Num posto **offline**, copia essa pasta antes.

### Docker

```bash
docker compose up --build -d                            # CPU
docker compose --profile gpu up --build -d faceai-gpu   # GPU NVIDIA (requer nvidia-container-toolkit)
```

Para alterações locais (portas, redes), usa um `docker-compose.override.yml`, que é ignorado pelo git. Assim, o `git pull` não entra em conflito com a tua configuração.

### Configuração (variáveis de ambiente)

| Variável | Por omissão | Descrição |
|---|---|---|
| `FACEAI_DATA_DIR` | `./data` | Casos, evidências, base de dados, `audit.jsonl` |
| `FACEAI_ENGINE` | `onnx` | `onnx` ou `deepface` (este exige o extra `deepface`) |
| `FACEAI_MODELS_DIR` | `~/.faceai/models` | Pesos ONNX |
| `FACEAI_UPLOAD_CHUNK_MB` | `8` | Tamanho de cada bloco do upload (o Cloudflare limita os pedidos a 100 MB) |
| `FACEAI_PRELOAD_MODELS` | `1` | Descarregar/carregar os modelos em segundo plano no arranque |

---

## Utilização

1. **Novo caso:** indica o nome ou referência do processo e o teu nome de analista (no cabeçalho). O nome e a descrição podem ser editados depois (✏️ ao lado do nome do caso). Os valores anteriores ficam na auditoria.
2. **Referências:** arrasta as imagens da pessoa. Cada uma é validada e é mostrada a face recortada.
3. **Vídeos:** arrasta os ficheiros de CCTV. O envio é **em blocos e retomável**: se a ligação cair, volta a largar o mesmo ficheiro e o upload continua de onde parou, mesmo depois de recarregar a página. Os metadados são extraídos automaticamente. Se o browser não conseguir reproduzir o vídeo e houver `ffmpeg`, é criada uma **cópia H.264 só para visualização**. A análise usa sempre o original. Os casos típicos são H.265/HEVC, `.dav`, `.avi`/MPEG-4, H.264 a 10 bits ou 4:2:2/4:4:4, que dão a mensagem "nenhum vídeo com formato e tipo MIME suportado".
   - A decisão usa o `ffprobe` (codec e formato de cor), e não só a extensão.
   - As conversões correm numa fila, uma de cada vez. A coluna **Reprodução no browser** mostra o estado e o progresso: em fila, a converter, pronta ou falhou.
   - As conversões interrompidas por um reinício são retomadas no arranque. Os vídeos importados por versões anteriores são avaliados nessa altura.
   - O botão ↻ cria ou recria a cópia de um vídeo. "Criar cópias de reprodução de todos os vídeos" faz o mesmo para o caso inteiro.
   - Se o browser não reproduzir um vídeo, ficam logo em fila as cópias de **todos os vídeos do caso**, porque os vídeos do mesmo DVR têm o mesmo formato.
   - Se, mesmo assim, o browser não reproduzir o vídeo nos resultados, a cópia certa é pedida **automaticamente**:
     - H.264/MP4 por omissão;
     - **VP9/WebM** se o browser não tiver H.264, como alguns Firefox/Chromium em Linux.

     O aviso mostra o diagnóstico: erro do browser, contentor e codec reais do original, e codecs suportados pelo browser.
   - O `ffprobe` também deteta as exportações de DVR com extensão `.mp4` que são, na verdade, H.264 "cru".
4. **Análises:** escolhe o modo (**procurar uma pessoa** com amostra, ou **descobrir todas as faces** sem amostra), os vídeos, as referências, o modelo, os fps e o limiar. Os interruptores ativam ou desativam o **pré-filtro de movimento**, a **cascata** e o **tracking**. O progresso é atualizado ao vivo, com o **tempo restante estimado** e a hora prevista de fim. A estimativa usa o ritmo do último minuto, porque frames sem movimento ou sem faces são muito mais rápidos do que frames com várias faces.
5. **Resultados:**
   - player com linha temporal e tabela de segmentos, e botões ◀ ▶ para passar ao vídeo anterior ou seguinte;
   - **vista por tracks**: um cartão por pessoa ou trajetória, com revisão do track inteiro;
   - **vista por deteções**, ordenável por tempo, semelhança ou **qualidade**;
   - **frame completo** (botão 🔍): com marcação ou original, e download de qualquer um deles. Ver [Download do frame](#download-do-frame).
6. **Eliminar um caso** (🗑 na lista de casos ou ao lado do nome do caso): pede o motivo e a confirmação, escrevendo o número do caso. Recusa se houver análises a decorrer. Cancela as conversões em curso e apaga do servidor os **vídeos originais e as cópias de reprodução**, as referências, as análises (deteções, recortes, frames) e os uploads por concluir. A auditoria mantém o histórico e regista um `case.deleted` com o motivo e a lista dos ficheiros apagados. Exporta antes os relatórios de que precises.
7. **Eliminar uma análise** (🗑 na lista de análises ou "Eliminar" nos resultados): pede o motivo e a confirmação, escrevendo o número da análise. Apaga as deteções, os tracks, as revisões e os ficheiros gerados. Os vídeos e as referências do caso mantêm-se. A auditoria é *append-only*: guarda o histórico da análise e regista a eliminação (quem, quando, motivo, parâmetros e totais). Não é possível eliminar uma análise a decorrer; é preciso cancelá-la primeiro.
8. **Exportar:** relatório PDF ou HTML, com estatísticas, parâmetros, tracks e qualidade; CSV; JSON; ou ZIP.

### Modo descoberta (sem amostra)

Quando não há nenhuma fotografia da pessoa (por exemplo, para saber *quem* passou por uma câmara), escolhe-se **Descobrir todas as faces** em *Nova análise*. Se o caso não tiver amostras válidas, este é o modo por omissão.

1. Todas as faces que passam os filtros (tamanho, confiança, qualidade) são seguidas entre frames (o **tracking é obrigatório** neste modo). De cada track guarda-se a primeira face, as que melhoram a qualidade e uma a cada 10 s.
2. Cada track é representado pela média, ponderada pela qualidade, das suas 5 melhores faces.
3. No fim, os tracks de todos os vídeos são **agrupados em pessoas** por centróides: cada track junta-se ao grupo cujo centro está a uma distância de cosseno ≤ **limiar de agrupamento**, ou abre um grupo novo. Depois, grupos cujos centros convergiram são fundidos. Ao contrário do *single linkage*, um track intermédio não encadeia duas pessoas diferentes. A pessoa #1 é a que mais aparece.
4. Nos resultados, a vista **Pessoas** mostra um cartão por pessoa com a melhor face, os tracks, as faces, os vídeos e a primeira aparição. Selecionar uma pessoa filtra a linha temporal, as aparições, os tracks e as faces.
5. **Correção do agrupamento,** que fica registada na auditoria:
   - **juntar** duas pessoas que são a mesma;
   - **mover** um track para outra pessoa ou para uma pessoa nova;
   - **confirmar** ou **rejeitar** uma pessoa inteira (por exemplo, uma falsa deteção).
6. **Amostra:** o recorte de qualquer face (ampliado se for pequeno) pode ser adicionado às imagens de referência do caso. A partir daí, uma análise normal procura essa pessoa noutros vídeos.

Os embeddings dos tracks ficam em `jobs/job_NNNNN/track_embeddings.npz`. O relatório, o CSV (`person_id`) e o JSON (`persons`, com as aparições e a hora real) incluem as pessoas.

> O agrupamento é uma ferramenta de **triagem**. A mesma pessoa pode surgir em mais de um grupo (ângulos, iluminação, oclusões) e pessoas parecidas podem ficar juntas. Um limiar mais baixo separa mais.

### Download do frame

No frame completo de uma deteção (🔍) há dois downloads:
- **Frame com marcação (JPEG):** o frame guardado na análise, com a caixa e a etiqueta.
- **Frame original (PNG):** o frame extraído do vídeo analisado, sem marcações e sem perdas em relação ao frame descodificado. Para garantir que é o mesmo frame que a análise viu, é comparado com o recorte da face guardado na análise:
  - o salto direto (*seek*) é aceite se o recorte coincidir;
  - senão, procura-se um vizinho que seja o único a coincidir;
  - senão, o vídeo é lido sequencialmente até ao frame N, como na análise. É exato, mas mais lento em vídeos longos.

O nome do ficheiro identifica o caso, a análise, o vídeo, o frame, o tempo e a deteção. Cada download fica na auditoria com o **SHA-256** do ficheiro entregue e o método de extração.

### Hora real do CCTV

Por omissão, os tempos são relativos ao início do ficheiro (`00:01:23.400`). Em **Vídeos → Acertar hora** há três formas de os converter em hora real:

1. **OCR da data/hora gravada na imagem.** A zona da data/hora é detetada automaticamente, ou marcada arrastando o rato sobre o frame. O OCR usa o RapidOCR (PP-OCR em ONNX Runtime, com os modelos incluídos no pacote, sem downloads) e lê ~12 frames distribuídos pelo vídeo. A seguir faz-se um **ajuste robusto**:
   - as leituras erradas são descartadas;
   - se as leituras cobrirem pelo menos 60 s, estima-se também a **taxa** do relógio. Isto corrige vídeos exportados com um fps nominal diferente do de gravação; por exemplo, um DVR que grava a 12,5 fps e exporta a 25 fps.

   Formatos suportados: `DD-MM-AAAA`, `MM/DD/AAAA` com AM/PM, `AAAA-MM-DD`, ano com 2 dígitos e meses por extenso (`12 Out 2026`). Também são corrigidas confusões típicas do OCR (O→0, l→1). Na interface vê-se cada leitura e se foi aceite ou descartada.
2. **Acerto manual:** escolhe-se um frame e indica-se a data/hora que ele mostra.
3. **Desvio do relógio do DVR:** se o relógio do DVR estava adiantado ou atrasado (por exemplo, confirmado com o operador ou com uma referência externa), indica-se a correção em segundos. Se estava 2 min adiantado, o desvio é −120 s.

A hora real aparece nos resultados, nos tracks e nos segmentos, e é exportada no CSV e no JSON (`real_time`, `real_start`, `real_end`). O relatório tem uma tabela com o método, o desvio, o nº de leituras consistentes e o erro máximo. Cada alteração fica na auditoria, com as leituras de OCR. A precisão típica é de ±1 s, porque o relógio impresso só mostra segundos inteiros.

### Afinação para CCTV

| Situação | Ajuste |
|---|---|
| Faces pequenas (câmara afastada) | `Ampliação da deteção` 1,5–2 (aumenta a resolução de entrada do SCRFD) e `Tamanho mínimo` 12–16 px |
| Muitos falsos positivos | baixar o limiar (ex.: 0,55) e/ou usar `Qualidade mínima` 0,3 |
| A pessoa passa muito depressa | aumentar os *frames por segundo* (4–5); o filtro de movimento mantém o custo baixo |
| Câmara com ruído, chuva ou árvores (o filtro de movimento não poupa) | aumentar o `Limiar de movimento` (0,005–0,01) |
| Validação cruzada | repetir com `arcface_mbf` ou com o motor DeepFace (`Facenet512`) |

---

## Considerações forenses e legais

- **Auditoria:** todas as ações ficam registadas com utilizador e hora UTC, na base de dados e em `data/audit.jsonl` (append-only). Inclui criação do caso, uploads (incluindo blocos iniciados ou cancelados), parâmetros, início e fim de análise, revisões de deteções e de tracks, e exportações. O registo é incluído no relatório e no ZIP.
- **Reprodutibilidade:** o relatório inclui os parâmetros completos (filtro de movimento, cascata, tracking, limiares) e as versões do Python, ONNX Runtime, OpenCV e NumPy. Inclui também o **SHA-256 de cada modelo** usado e o dispositivo.
- **Evidências:** os ficheiros originais são guardados sem alterações. A cópia de reprodução é um ficheiro separado.
- **Transparência dos filtros:** o relatório indica quantos frames foram saltados por falta de movimento e por não terem faces na 1.ª passagem. Um frame saltado não foi analisado pelo modelo completo. Se for preciso, repete a análise com os filtros desligados.
- **Limitações:** a precisão cai muito com baixa resolução, compressão forte, perfil, oclusões, iluminação infravermelha e *motion blur*. O score de qualidade ajuda a identificar estes casos. Os modelos têm enviesamentos demográficos documentados. Um "não encontrado" **não prova ausência**.
- **Proteção de dados:** a análise biométrica é tratamento de dados de categoria especial (RGPD art. 9.º; Lei n.º 59/2019 no contexto de investigação criminal). Os dados ficam locais. Se publicares a app (por exemplo, com Cloudflare Tunnel), protege-a com autenticação (Cloudflare Access).
- **Licenças:** o código desta aplicação e o pós-processamento SCRFD e ArcFace (adaptado do InsightFace) são MIT. Os **pesos pré-treinados do InsightFace** (`buffalo_l`, `buffalo_sc`) são disponibilizados **apenas para investigação e uso não comercial**. Para uso comercial, usa o motor DeepFace ou modelos com licença adequada.

---

## Estrutura

```
backend/
  app/
    main.py                 FastAPI (+ serve frontend/dist)
    config.py, db.py        configuração; SQLite com migração aditiva automática
    models.py, schemas.py   Case, ReferenceImage, Video, Job, Detection, Track, Upload, AuditEvent
    api/                    cases.py, jobs.py (análises, tracks, revisão, exportação), uploads.py (blocos), system.py
    services/
      face_engine.py        interface comum dos motores e seleção do motor ativo
      engine_onnx.py        SCRFD + ArcFace (ONNX Runtime), download verificado por SHA-256
      engine_deepface.py    motor DeepFace/TensorFlow (opcional)
      motion.py             pré-filtro de movimento
      quality.py            score de qualidade da face
      tracking.py           tracker IoU + embedding
      clustering.py, persons.py   modo descoberta: agrupamento de tracks em pessoas
      frames.py             extração verificada do frame original para download
      jobs.py               pipeline de análise em background (movimento → cascata → deteção → qualidade → tracking)
      matcher.py, segments.py, references.py, video_processor.py, reports.py, transcode.py, audit.py
    templates/report.html.j2
  tests/                    pytest (motor falso + testes com modelos reais)
frontend/src/
  pages/                    CaseList, CaseView, Results
  components/               JobsPanel, PersonCard, TrackCard, DetectionCard, QualityBadge, Timeline, ...
```

## Testes

```bash
cd backend
pytest                                   # rápidos, com motor facial falso
FACEAI_E2E_DATASET=/caminho/deepface/tests/unit/dataset pytest -m e2e   # modelos reais do motor ativo
```

## Próximos passos possíveis

- Hash SHA-256 das evidências na importação e log de auditoria encadeado.
- Identidade do analista a partir do Cloudflare Access.
- Comparar pessoas descobertas entre análises diferentes (os embeddings já ficam guardados).
- Várias pessoas por caso numa única passagem pelo vídeo.
