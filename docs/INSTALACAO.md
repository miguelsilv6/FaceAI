# Instalação do FaceAI

Este guia cobre três formas de instalar o FaceAI numa VM ou num posto de análise:

- **A. Instalação nativa** (Ubuntu/Debian): a forma recomendada.
- **B. Docker**, em CPU ou GPU.
- **C. Posto offline / isolado**, para análise forense sem rede.

## Requisitos mínimos

| | Mínimo | Recomendado (CPU) | Com GPU |
|---|---|---|---|
| vCPU | 2 | 4–8 | 4 |
| RAM | 4 GB | 8 GB | 16 GB |
| GPU | — | — | NVIDIA com ≥ 4 GB de VRAM |
| Disco (sistema + app) | 10 GB | 20 GB | 25 GB |
| SO | Ubuntu 22.04/24.04 ou Debian 12, x86-64 | | |

Além disto, é preciso espaço para os vídeos importados.

Valores de referência medidos (4 vCPU, sem GPU, RetinaFace + ArcFace): ~1,6 s por frame analisado e ~1,4 GB de RAM. Uma hora de vídeo a 1 frame/s demora ~1,6 h.

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

A instalação descarrega cerca de 2,8 GB (sobretudo o TensorFlow) e pode demorar alguns minutos.

### 5. Frontend (interface web)

```bash
cd frontend
npm ci
npm run build          # gera frontend/dist, que o backend serve
cd ..
```

### 6. Descarregar os modelos (uma vez, ~250 MB)

Sem este passo, os modelos são descarregados na primeira análise. Fazê-lo já evita esperas e confirma que o TensorFlow funciona:

```bash
cd backend
python -c "
import numpy as np
from app.services.face_engine import DeepFaceEngine, _environment
DeepFaceEngine().detect(np.zeros((200, 200, 3), np.uint8), model='ArcFace', detector='retinaface')
print('Modelos prontos. Dispositivo:', _environment()['device'])
"
```

Os pesos ficam em `~/.deepface/weights/`. Para mudar o local, define a variável `DEEPFACE_HOME`.

### 7. Arrancar

```bash
cd backend
FACEAI_DATA_DIR=/srv/faceai/data uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Abre **http://localhost:8000** no browser.

- Para aceder a partir do teu computador sem expor a porta na rede, usa um túnel SSH: `ssh -L 8000:localhost:8000 utilizador@vm`. Depois abre `http://localhost:8000` no teu computador.
- `FACEAI_DATA_DIR` é onde ficam os casos, as evidências, a base de dados e o `audit.jsonl`. Escolhe um disco com espaço e com backup.

### 8. Verificar

```bash
curl -s http://127.0.0.1:8000/api/system | python3 -m json.tool | head -20
```

A resposta deve mostrar `"device": "CPU"` (ou `"GPU"`) e as versões do DeepFace e do TensorFlow.

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
# copia ~/.deepface para /srv/faceai/.deepface se já descarregaste os modelos
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

Abre **http://localhost:8000**.
- Os dados ficam em `./data`.
- Os modelos ficam no volume `faceai-models` e são descarregados na primeira análise.

**GPU NVIDIA:** instala o driver NVIDIA e o [nvidia-container-toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html), e depois:

```bash
docker compose --profile gpu up --build -d faceai-gpu
```

---

## GPU (instalação nativa)

1. Instala o driver NVIDIA (`sudo ubuntu-drivers install` e reinicia). Confirma com `nvidia-smi`.
2. Instala com `pip install -e "backend[gpu]"`. Isto instala o TensorFlow com as bibliotecas CUDA/cuDNN via pip, por isso não é preciso instalar o CUDA no sistema.
3. Confirma que a GPU é detetada:
   ```bash
   python -c "import tensorflow as tf; print(tf.config.list_physical_devices('GPU'))"
   ```
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
   cp -r ~/.deepface deepface                       # pesos dos modelos
   pip download -d wheels "./FaceAI/backend[cpu]"   # pacotes Python
   cd .. && tar czf faceai-offline.tgz faceai-offline
   ```
3. No posto isolado, instala os pacotes `apt` do passo A.1 a partir de um mirror local ou ISO. Depois:
   ```bash
   tar xzf faceai-offline.tgz && cd faceai-offline
   cp -r deepface ~/.deepface                       # modelos
   python3 -m venv .venv && source .venv/bin/activate
   pip install --no-index --find-links wheels -e "FaceAI/backend[cpu]"
   ```
   O frontend já vai compilado em `frontend/dist`, por isso o Node não é necessário no posto.
4. Arranca como no passo A.7.

A aplicação não envia imagens nem vídeos para fora. A rede só é usada para instalar dependências e descarregar os modelos.

---

## Problemas comuns

| Sintoma | Causa / solução |
|---|---|
| `A KerasTensor cannot be used as input to a TensorFlow function` | Falta o `tf-keras`, ou o TensorFlow foi importado antes de definir `TF_USE_LEGACY_KERAS=1`. Reinstala com `pip install -e "backend[cpu]"`. A app já define a variável no arranque. |
| Download dos pesos falha (`An exception occurred while downloading ...`) | Sem acesso a `github.com`. Copia `~/.deepface/weights` de outra máquina (ver C). O detetor YuNet descarrega os pesos de outro sítio: se falhar, usa o RetinaFace. |
| PDF não é gerado (`cannot load library 'libpango'`) | Falta `libpango-1.0-0 libpangoft2-1.0-0` (passo A.1). |
| O vídeo não reproduz no browser | Codec não suportado e ffmpeg não instalado. Instala o ffmpeg e volta a importar o vídeo. A análise funciona na mesma. |
| `Não foi possível abrir o vídeo` ao importar | Formato proprietário de DVR (ex.: `.dav`). Converte primeiro: `ffmpeg -i entrada.dav -c:v libx264 saida.mp4`. Regista a conversão no relatório. |
| Análise muito lenta | Reduz *frames por segundo* (0,5–1), evita a *ampliação* > 1, ou usa GPU. |
| `ImportError: libGL.so.1` | `sudo apt install libgl1` |
