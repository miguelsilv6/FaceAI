# Instalação do FaceAI

Este guia cobre três formas de instalar o FaceAI numa VM ou num posto de análise:

- **A. Instalação nativa** (Ubuntu/Debian): a forma recomendada.
- **B. Docker**, em CPU ou GPU.
- **C. Posto offline / isolado**, para análise forense sem rede.

## Requisitos mínimos

| | Mínimo | Recomendado (CPU) | Com GPU |
|---|---|---|---|
| vCPU | 2 | 4–8 | 4 |
| RAM | 2 GB | 4–8 GB | 8 GB |
| GPU | — | — | NVIDIA com ≥ 2 GB de VRAM |
| Disco (sistema + app) | 5 GB | 10 GB | 15 GB |
| SO | Ubuntu 22.04/24.04 ou Debian 12, x86-64 | | |

Além disto, é preciso espaço para os vídeos importados.

Valores de referência medidos (4 vCPU, sem GPU, motor ONNX: SCRFD-10G + ArcFace R50):
- ~0,07 s por frame de deteção, mais ~0,08 s por face;
- ~0,56 GB de RAM.

Com o pré-filtro de movimento e a cascata (ativos por omissão), só os frames com mudanças e com faces passam pelo modelo completo. Num vídeo de teste, isto reduziu o tempo ~8×.

A versão anterior (DeepFace/TensorFlow) precisava de ~1,6 s por frame e ~1,4 GB de RAM. Continua disponível como motor opcional.

---

## A. Instalação nativa (Ubuntu 24.04 / Debian 12)

### 1. Pacotes do sistema

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip ffmpeg \
    libgl1 libglib2.0-0 libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core
```

Para que servem:
- `ffmpeg`: cria a cópia de reprodução H.264 dos vídeos de CCTV;
- `libpango*` e `libharfbuzz-subset0`: geração do relatório PDF (WeasyPrint);
- `libgl1` e `libglib2.0-0`: OpenCV.

> **Ubuntu 22.04:** o Python do sistema é o 3.10 e a app precisa do **3.11 ou superior**. Instala o 3.11 e usa `python3.11` nos passos seguintes:
> ```bash
> sudo add-apt-repository -y ppa:deadsnakes/ppa
> sudo apt install -y python3.11 python3.11-venv
> ```

### 2. Node.js (só para compilar a interface)

```bash
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs
node --version    # deve ser v20 ou superior
```

### 3. Obter o código

```bash
git clone https://github.com/miguelsilv6/FaceAI.git
cd FaceAI
git checkout claude/determined-cannon-c2q29b    # até existir um branch main
```

### 4. Backend (Python)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e "backend[cpu]"           # só CPU
# pip install -e "backend[gpu]"         # GPU NVIDIA (ver secção "GPU")
```

A instalação ocupa cerca de 0,5 GB (ONNX Runtime + OpenCV; sem TensorFlow).

Opcional: o motor DeepFace/TensorFlow (`pip install -e "backend[cpu,deepface]"` e `FACEAI_ENGINE=deepface`) acrescenta ~2,3 GB.

### 5. Frontend (interface web)

```bash
cd frontend
npm ci
npm run build          # gera frontend/dist, que o backend serve
cd ..
```

### 6. Descarregar os modelos (uma vez, ~198 MB)

Sem este passo, os modelos são descarregados na primeira análise. Fazê-lo já evita esperas e confirma que o ONNX Runtime funciona:

```bash
cd backend
python -c "
import numpy as np
from app.services.engine_onnx import OnnxEngine, _environment
e = OnnxEngine()
for m, d in [('arcface_r50', 'scrfd_10g'), ('arcface_mbf', 'scrfd_500m')]:
    e.detect(np.zeros((200, 200, 3), np.uint8), model=m, detector=d)
print('Modelos prontos. Dispositivo:', _environment()['device'])
"
```

Os pesos ficam em `~/.faceai/models/`. Para mudar o local, define `FACEAI_MODELS_DIR`.

O **complemento por vestuário** usa mais dois modelos, descarregados na primeira análise que o ativa (~45 MB):
- YOLOX dos releases do projeto YOLOX → `models/yolox/`;
- OSNet-AIN do release `models-reid-v1` deste repositório → `models/reid/`.

Para os descarregar já:

```bash
python -c "
import numpy as np
from app.services.body_engine import OnnxBodyEngine
e = OnnxBodyEngine()
e.detect_persons(np.zeros((200, 200, 3), np.uint8), detector='yolox_s')
e.embed([np.zeros((256, 128, 3), np.uint8)])
print('Modelos do vestuário prontos.')
"
```

Cada ficheiro é verificado por SHA-256 contra os hashes fixados em `backend/app/services/engine_onnx.py`. Se algum não coincidir, é descartado.

### 7. Arrancar

```bash
cd backend
FACEAI_DATA_DIR=/srv/faceai/data uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Antes do primeiro login, cria o administrador (noutro terminal, com o venv ativo):

```bash
cd backend
FACEAI_DATA_DIR=/srv/faceai/data python -m app.cli create-admin chefe --name "Nome Apelido"
```

Abre **http://localhost:8000** no browser e entra com essa conta. Recomendado: em *A minha conta* → *Ativar verificação em dois passos*, lês o QR code com uma app autenticadora (Microsoft/Google Authenticator, Aegis, 2FAS…) e guardas os 10 códigos de recuperação. O MFA é opcional por utilizador; com `FACEAI_REQUIRE_MFA=true` passa a ser obrigatório para todos e é configurado logo no primeiro login. Os restantes utilizadores são criados na interface (menu do utilizador → *Utilizadores*) com uma password temporária, que mudam no primeiro login.

- **Backup da chave:** o ficheiro `$FACEAI_DATA_DIR/secret.key` (ou a variável `FACEAI_SECRET_KEY`) cifra os segredos MFA. Inclui-o nos backups; sem ele, quem tem MFA ativo tem de o reconfigurar (`python -m app.cli reset-mfa <utilizador>`).
- **HTTP numa LAN:** o cookie de sessão só leva a flag `Secure` quando o pedido chega por HTTPS (`FACEAI_COOKIE_SECURE=auto`). Num acesso por `http://` na rede local a sessão funciona, mas a password e o cookie passam em claro: prefere o túnel SSH abaixo ou HTTPS.
- Para aceder a partir do teu computador sem expor a porta na rede, usa um túnel SSH: `ssh -L 8000:localhost:8000 utilizador@vm`. Depois abre `http://localhost:8000` no teu computador.
- `FACEAI_DATA_DIR` é onde ficam os casos, as evidências, a base de dados e o `audit.jsonl`. Escolhe um disco com espaço e com backup.

### 8. Verificar

```bash
curl -s http://127.0.0.1:8000/api/auth/status      # {"setup_required": false} depois de criares o admin
```

Depois do login, o distintivo no cabeçalho mostra o dispositivo (CPU ou GPU). Os restantes endpoints da API exigem sessão.

Opcionalmente, corre os testes:

```bash
source .venv/bin/activate
pip install -e "backend[cpu,dev]"
cd backend && pytest -q
```

### 9. (Opcional) Arrancar automaticamente com o sistema (systemd)

```bash
sudo useradd --system --create-home --home-dir /srv/faceai faceai
sudo mkdir -p /srv/faceai/data && sudo chown -R faceai:faceai /srv/faceai
# copia ~/.faceai/models para /srv/faceai/models se já descarregaste os modelos
```

Cria `/etc/systemd/system/faceai.service`, ajustando `/opt/FaceAI` ao local do código:

```ini
[Unit]
Description=FaceAI
After=network.target

[Service]
User=faceai
WorkingDirectory=/opt/FaceAI/backend
Environment=FACEAI_DATA_DIR=/srv/faceai/data
Environment=DEEPFACE_HOME=/srv/faceai
ExecStart=/opt/FaceAI/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now faceai
journalctl -u faceai -f      # ver logs
```

---

## B. Docker

Requisitos: Docker Engine com o plugin Compose.

```bash
git clone https://github.com/miguelsilv6/FaceAI.git
cd FaceAI
git checkout claude/determined-cannon-c2q29b

docker compose up --build -d          # CPU
docker compose logs -f
```

Cria o primeiro administrador e abre **http://localhost:8000**:

```bash
docker compose exec faceai python -m app.cli create-admin chefe --name "Nome Apelido"
```

- Os dados ficam em `./data`, incluindo `secret.key` (a chave dos segredos MFA): faz backup da pasta inteira.
- Os modelos ficam no volume `faceai-models` e são descarregados na primeira análise.

**GPU NVIDIA:** instala o driver NVIDIA e o [nvidia-container-toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html), e depois:

```bash
docker compose --profile gpu up --build -d faceai-gpu
```

**Windows (Docker Desktop + WSL2):** não é preciso instalar o nvidia-container-toolkit, mas é preciso:
1. o driver NVIDIA **para Windows** atualizado (com suporte WSL). Confirma com `nvidia-smi` no PowerShell. Não instales drivers NVIDIA dentro do WSL;
2. o WSL atualizado: `wsl --update` e depois `wsl --shutdown`;
3. no Docker Desktop, *Settings → General → Use the WSL 2 based engine* ativo. O backend Hyper-V não suporta GPU.

Testa primeiro sem o FaceAI:

```powershell
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

Se este teste falhar, o problema está no anfitrião e não no FaceAI. Até lá, usa o serviço CPU: `docker compose --profile gpu down` e depois `docker compose up --build -d`. Os dados mantêm-se.

---

## GPU (instalação nativa)

1. Instala o driver NVIDIA (`sudo ubuntu-drivers install` e reinicia). Confirma com `nvidia-smi`.
2. Instala com `pip install -e "backend[gpu]"` (em vez de `[cpu]`). Isto instala o `onnxruntime-gpu` com as bibliotecas CUDA/cuDNN via pip, por isso não é preciso instalar o CUDA no sistema.
3. Confirma que a GPU é detetada:
   ```bash
   python -c "import onnxruntime as ort; print(ort.get_available_providers())"
   ```
   A lista deve incluir `CUDAExecutionProvider`.
   O badge **GPU** aparece no cabeçalho da interface.

**Numa VM**, a GPU só fica disponível com *passthrough* PCI (Proxmox, VMware ESXi, Hyper-V DDA). O VirtualBox não suporta. No Windows, a alternativa é o WSL2, que já expõe a GPU NVIDIA.

---

## C. Posto offline / isolado

Num posto de análise sem acesso à Internet:

1. Num computador **com** rede e o mesmo SO e versão de Python, faz os passos A.1–A.6.
2. Recolhe as dependências e os modelos:
   ```bash
   mkdir -p ~/faceai-offline && cd ~/faceai-offline
   cp -r /caminho/para/FaceAI .                     # inclui frontend/dist já compilado
   rm -rf FaceAI/.venv FaceAI/frontend/node_modules
   cp -r ~/.faceai/models models                    # pesos dos modelos (passo A.6)
   pip download -d wheels "./FaceAI/backend[cpu]"   # pacotes Python
   cd .. && tar czf faceai-offline.tgz faceai-offline
   ```
3. No posto isolado, instala os pacotes `apt` do passo A.1 a partir de um mirror local ou ISO. Depois:
   ```bash
   tar xzf faceai-offline.tgz && cd faceai-offline
   mkdir -p ~/.faceai && cp -r models ~/.faceai/models   # modelos
   python3 -m venv .venv && source .venv/bin/activate
   pip install --no-index --find-links wheels -e "FaceAI/backend[cpu]"
   ```
   O frontend já vai compilado em `frontend/dist`, por isso o Node não é necessário no posto.
4. Arranca como no passo A.7.

A aplicação não envia imagens nem vídeos para fora. A rede só é usada para instalar dependências e descarregar os modelos.

---

## D. Atualizar uma instalação existente

A base de dados é migrada automaticamente no arranque: as colunas novas são acrescentadas e nenhum dado é apagado. Os casos e as análises anteriores mantêm-se.

**Ao atualizar para a versão com contas:** depois do arranque, a API responde 503 até criares o primeiro administrador (`python -m app.cli create-admin`, ou com `docker compose exec faceai …`). Os casos antigos não têm equipa: só os administradores os veem. Atribui-lhes um responsável no separador *Equipa*. Os eventos de auditoria antigos mantêm o nome livre que era indicado no navegador.

**Instalação nativa:**
```bash
cd ~/FaceAI && git pull
source .venv/bin/activate && pip install -e "backend[cpu]"
cd frontend && npm ci && npm run build
sudo systemctl restart faceai         # ou volta a arrancar o uvicorn
```
Opcional: `pip uninstall -y tensorflow tf-keras deepface retina-face` liberta ~2,3 GB, se não fores usar o motor DeepFace.

**Docker:** se alteraste o `docker-compose.yml` (portas, redes), passa essas alterações para um `docker-compose.override.yml`, que é ignorado pelo git, e repõe o original:
```bash
cd ~/FaceAI
cp docker-compose.yml /tmp/compose.meu.yml       # cópia de segurança
git checkout docker-compose.yml && git pull
cat > docker-compose.override.yml <<'YML'
services:
  faceai:
    ports: !override
      - "127.0.0.1:8002:8000"
YML
docker compose up -d --build
```
O `!override` substitui a lista de portas do ficheiro base, em vez de a juntar, e requer Docker Compose 2.24 ou superior (`docker compose version`).

---

## E. Publicar com Cloudflare Tunnel

1. Arranca a app apenas em localhost (por exemplo, `127.0.0.1:8002`).
2. No `/etc/cloudflared/config.yml` do túnel, acrescenta a regra **antes** de `http_status:404`:
   ```yaml
     - hostname: faceai.exemplo.pt
       service: http://localhost:8002
   ```
3. Reinicia o túnel com `systemctl restart cloudflared` e cria o CNAME `faceai` → `<ID-DO-TÚNEL>.cfargotunnel.com`.
4. Define no serviço `FACEAI_TRUST_PROXY=cloudflare`, para a auditoria e os bloqueios usarem o IP real (`CF-Connecting-IP`) e o cookie ser marcado `Secure`. Opcionalmente, define também `FACEAI_ALLOWED_ORIGINS=https://faceai.exemplo.pt`. Só uses `cloudflare` se a app **não** estiver acessível diretamente, porque esses cabeçalhos poderiam ser forjados.
5. **Recomendado (defesa em profundidade):** protege também o subdomínio com **Cloudflare Access** (Zero Trust → Access → Applications → Self-hosted), com uma policy *Allow* para os emails autorizados. A app tem login próprio (com MFA opcional), mas assim nem chega a ficar exposta a quem não está autorizado.

Os vídeos são enviados em blocos de 8 MB (`FACEAI_UPLOAD_CHUNK_MB`), por isso o limite de 100 MB por pedido do Cloudflare não afeta o upload.

---

## Problemas comuns

| Sintoma | Causa / solução |
|---|---|
| Download dos modelos falha (`urlopen error` / `SHA-256 inválido`) | Sem acesso a `github.com`, ou download corrompido. Repete, ou copia `~/.faceai/models` de outra máquina (ver C). |
| `No module named 'onnxruntime'` | Falta o extra: `pip install -e "backend[cpu]"` (ou `[gpu]`). |
| `A KerasTensor cannot be used…` (só com `FACEAI_ENGINE=deepface`) | Falta o `tf-keras`. Reinstala com `pip install -e "backend[cpu,deepface]"`. |
| "OCR indisponível" ao acertar a hora | Falta o pacote `rapidocr`: corre `pip install -e "backend[cpu]"` outra vez. |
| O OCR não encontra a data/hora | Marca a zona à mão (arrastando o rato sobre o frame), escolhe o formato da data certo, ou usa o acerto manual. |
| Upload de vídeo grande falha atrás do Cloudflare (413) | Versão antiga sem upload em blocos. Atualiza a app e recarrega a página (Ctrl+F5); confirma `FACEAI_UPLOAD_CHUNK_MB` < 100. |
| PDF não é gerado (`cannot load library 'libpango'`) | Falta `libpango-1.0-0 libpangoft2-1.0-0` (passo A.1). |
| O vídeo não reproduz no browser | Codec não suportado e ffmpeg não instalado. Instala o ffmpeg e volta a importar o vídeo. A análise funciona na mesma. |
| `Não foi possível abrir o vídeo` ao importar | Formato proprietário de DVR (ex.: `.dav`). Converte primeiro: `ffmpeg -i entrada.dav -c:v libx264 saida.mp4`. Regista a conversão no relatório. |
| Análise muito lenta | Reduz *frames por segundo* (0,5–1), evita a *ampliação* > 1, ou usa GPU. |
| `nvidia-container-cli: … WSL environment detected but no adapters were found` | O Docker Desktop (WSL2) não vê nenhuma GPU NVIDIA. Atualiza o driver NVIDIA do Windows e o WSL (`wsl --update`, `wsl --shutdown`), e confirma o motor WSL 2 no Docker Desktop (ver B, "Windows"). Sem GPU NVIDIA, usa o serviço CPU (`docker compose up -d`). |
| `ImportError: libGL.so.1` | Está instalado o `opencv-python` em vez do `opencv-python-headless`. Corre `pip uninstall opencv-python`, ou `sudo apt install libgl1`. |
