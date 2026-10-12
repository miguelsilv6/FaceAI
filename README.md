# FaceAI — deteção de uma pessoa em vídeos de CCTV

Aplicação web local para apoio à investigação forense digital. A partir de uma ou mais **imagens de referência** da face de uma pessoa, analisa **vídeos de CCTV** importados e indica **se** essa pessoa aparece, **quando** (timestamps e intervalos) e **com que grau de semelhança**. Inclui revisão humana dos resultados, relatório PDF/HTML, exportação CSV/JSON/ZIP e log de auditoria.

> ⚠️ Os resultados de reconhecimento facial são **indicativos**. Não constituem identificação conclusiva e têm de ser validados por um perito.

---

## Soluções open source usadas (e alternativas)

| Camada | Escolha | Alternativas consideradas |
|---|---|---|
| Deteção facial | **RetinaFace** (via DeepFace) | MTCNN, YuNet, CenterFace, YOLOv8-face, SCRFD (InsightFace) |
| Reconhecimento (embeddings) | **ArcFace** (via DeepFace) | Facenet512, VGG-Face, GhostFaceNet, SFace; InsightFace `buffalo_l` (muito preciso, mas os modelos são só para uso não comercial); dlib/`face_recognition` (simples, mais fraco em CCTV) |
| Framework de reconhecimento | **[DeepFace](https://github.com/serengil/deepface)** (MIT) | InsightFace, CompreFace (serviço REST pronto), facenet-pytorch |
| Vídeo | **OpenCV** (amostragem de frames) + **ffmpeg** (cópia de reprodução) | PyAV, decord |
| Comparação | Distância de cosseno em NumPy | FAISS / Milvus, para milhões de faces |
| Backend | **FastAPI** + SQLModel/SQLite | Flask, Django |
| Frontend | **React + Vite + Mantine** | Gradio/Streamlit (protótipo), PySide6 (desktop) |
| Relatórios | **Jinja2 + WeasyPrint** | ReportLab |

A escolha do DeepFace permite trocar de **modelo** e de **detetor** na interface sem mudar código. Isto é útil para comparar resultados (por exemplo, confirmar um match ArcFace com Facenet512).

---

## Como funciona

```
Imagens de referência ──► detetor ──► embedding (ArcFace 512-d) ──┐
                                                                  ├─► distância de cosseno ──► limiar ──► deteção
Vídeo ──► frames a N fps ──► detetor ──► embedding de cada face ──┘                                          │
                                                                                                             ▼
                       segmentos de aparição ◄── agrupamento temporal ◄── revisão humana (confirmar/rejeitar)
```

1. **Referências.** Cada imagem tem de conter **exatamente uma face**. As imagens com 0 ou mais de 1 face ficam marcadas como inválidas e não são usadas. Ter várias imagens (ângulos, iluminação, idades diferentes) aumenta a sensibilidade.
2. **Vídeo.** Os frames são amostrados a uma taxa configurável (por omissão 2 fps). Os restantes frames são apenas descodificados (`grab`), o que poupa tempo.
3. **Comparação.** Cada face do frame é comparada com todas as referências e conta a menor distância. Há **match** quando `distância ≤ limiar`. Os limiares por omissão são os do DeepFace (ArcFace: 0,68). As faces até `limiar + margem` ficam guardadas como **candidatos** para revisão.
4. **Segmentos.** Matches separados por menos de *N* segundos são agrupados em intervalos de aparição.
5. **Prova.** Por cada deteção são guardados o recorte da face, o frame completo anotado, o timestamp, o nº de frame, a bounding box, a confiança do detetor, a distância e a similaridade.

---

## Instalação

📄 **Guia completo:** [`docs/INSTALACAO.md`](docs/INSTALACAO.md). Inclui os requisitos da VM, a instalação nativa passo a passo, o Docker, a GPU, um posto offline, o serviço systemd e a resolução de problemas.

### Resumo (desenvolvimento)

Requisitos: Python 3.11+, Node 20+, `ffmpeg` (opcional mas recomendado) e as bibliotecas do WeasyPrint (Pango).

```bash
# Backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e "backend[cpu,dev]"        # ou "backend[gpu,dev]" com GPU NVIDIA (CUDA)
cd backend && uvicorn app.main:app --reload --port 8000

# Frontend (noutro terminal)
cd frontend
npm install
npm run dev                               # http://localhost:5173 (encaminha /api para :8000)
```

Para usar só o backend a servir a interface, corre `npm run build` em `frontend/` e abre `http://localhost:8000`.

Na primeira análise, o DeepFace descarrega os pesos dos modelos (~250 MB) para `~/.deepface` (ou `$DEEPFACE_HOME`). Num posto **offline**, copia essa pasta antes.

### Docker

```bash
docker compose up --build                            # CPU
docker compose --profile gpu up --build faceai-gpu   # GPU NVIDIA (requer nvidia-container-toolkit)
```

Os dados ficam em `./data` e os modelos num volume Docker. A porta só fica exposta em `127.0.0.1`.

### GPU / CPU

A deteção é automática: se o TensorFlow encontrar uma GPU, usa-a. O dispositivo aparece no cabeçalho da interface e fica registado no relatório. Em CPU, conta com ~1–2 s por frame com RetinaFace + ArcFace. Ajusta *frames por segundo* conforme a duração do vídeo.

---

## Utilização

1. **Novo caso:** indica o nome ou referência do processo e o teu nome de analista (no cabeçalho; fica nos registos).
2. **Referências:** arrasta as imagens da pessoa. Cada uma é validada e é mostrada a face recortada.
3. **Vídeos:** arrasta os ficheiros de CCTV. São extraídos os metadados (duração, fps, resolução, codec). Se o codec não for reproduzível no browser e houver `ffmpeg`, é criada uma **cópia H.264 só para visualização**. A análise usa sempre o original.
4. **Análises:** escolhe vídeos, referências, modelo, detetor, fps e limiar, e inicia. O progresso é atualizado ao vivo e a análise pode ser cancelada.
5. **Resultados:**
   - player com uma linha temporal (os segmentos a vermelho são clicáveis);
   - tabela de segmentos;
   - galeria com cada deteção lado a lado com a referência mais próxima;
   - filtro por similaridade e opção para mostrar os candidatos;
   - botões **Confirmar / Rejeitar**, que ficam registados com o analista e a hora;
   - frame completo anotado.
6. **Exportar:** relatório PDF ou HTML, CSV, JSON, ou ZIP (relatório + CSV + JSON + recortes + frames + referências + auditoria).

### Afinação para CCTV

| Situação | Ajuste |
|---|---|
| Faces pequenas (câmara afastada) | `Ampliação dos frames` 1,5–2 e `Tamanho mínimo` 12–16 px |
| Muitos falsos positivos | baixar o limiar (ex.: 0,60 em ArcFace) ou aumentar a confiança mínima |
| A pessoa passa muito depressa | aumentar *frames por segundo* (4–5) |
| Vídeos longos em CPU | 0,5–1 fps numa primeira passagem, depois analisar só os vídeos com resultados |
| Validação cruzada | repetir a análise com `Facenet512` e comparar |

---

## Considerações forenses e legais

- **Auditoria:** todas as ações (criação de caso, uploads, parâmetros, início/fim de análise, revisões, exportações) ficam registadas com utilizador e hora UTC na base de dados e em `data/audit.jsonl` (append-only). O registo é incluído no relatório e no ZIP.
- **Reprodutibilidade:** o relatório inclui os parâmetros completos e as versões do Python, DeepFace, TensorFlow, OpenCV e NumPy, bem como o dispositivo usado.
- **Evidências:** os ficheiros originais são guardados sem alterações. A cópia de reprodução é um ficheiro separado.
- **Limitações:** a precisão cai muito com baixa resolução, compressão forte, perfil, oclusões (máscaras, bonés), iluminação infravermelha e *motion blur*. Os modelos têm enviesamentos demográficos documentados na literatura. Um "não encontrado" **não prova ausência**.
- **Proteção de dados:** a análise biométrica é tratamento de dados de categoria especial (RGPD art. 9.º; Lei n.º 59/2019 no contexto de investigação criminal). Usa a ferramenta apenas com base legal adequada. Os dados ficam locais: nenhuma imagem é enviada para serviços externos (só os pesos dos modelos são descarregados).
- **Licenças:** o código do DeepFace é MIT. Os pesos pré-treinados têm licenças próprias, definidas pelos respetivos autores. Para uso académico não há restrição prática, mas convém verificar antes de qualquer uso comercial.

---

## Estrutura

```
backend/
  app/
    main.py                 FastAPI (+ serve frontend/dist)
    config.py, db.py, models.py, schemas.py
    api/                    cases.py (casos, referências, vídeos, auditoria), jobs.py (análises, revisão, exportação), system.py
    services/
      face_engine.py        wrapper DeepFace, deteção GPU/CPU, versões
      references.py         validação e embeddings das referências
      video_processor.py    leitura/amostragem OpenCV, recortes, anotação
      matcher.py            distância de cosseno
      segments.py           agrupamento temporal
      jobs.py               fila em background, progresso, cancelamento
      reports.py            HTML/PDF/CSV/JSON/ZIP
      transcode.py          cópia de reprodução H.264 (ffmpeg)
      audit.py              log de auditoria
    templates/report.html.j2
  tests/                    pytest (motor falso rápido + teste e2e com modelos reais)
frontend/src/
  pages/                    CaseList, CaseView, Results
  components/               ReferencesPanel, VideosPanel, JobsPanel, Timeline, DetectionCard, AuditPanel, ...
```

## Testes

```bash
cd backend
pytest                                   # rápidos, com motor facial falso (sem TensorFlow)
FACEAI_E2E_DATASET=/caminho/deepface/tests/unit/dataset pytest -m e2e   # RetinaFace + ArcFace reais
```

O teste e2e gera um vídeo sintético em que a pessoa procurada aparece só entre os 4 e os 8 s, ao lado de outra pessoa. Verifica que é reportado exatamente esse segmento e nunca a outra pessoa.

## Próximos passos possíveis

- Hash SHA-256 das evidências no momento da importação (cadeia de custódia).
- Tracking (ByteTrack/SORT) para agrupar deteções da mesma pessoa e reduzir trabalho de revisão.
- Score de qualidade da face (desfoque, pose) para ordenar e filtrar deteções.
- Pesquisa "inversa": agrupar (clustering) todas as faces de um vídeo sem referência.
- Suporte direto a formatos proprietários de DVR (`.dav`, …) via conversão automática.
- Índice FAISS para pesquisar várias pessoas em grandes volumes de vídeo.
