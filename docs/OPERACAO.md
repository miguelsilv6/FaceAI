# Operação

Para quem administra o servidor: backups, restauro, atualizações, consola e registos. Instalação: [INSTALACAO.md](INSTALACAO.md).

Com Docker, os comandos da consola correm dentro do contentor: `docker compose exec faceai python -m app.cli <comando>`. Na instalação nativa: `cd backend && python -m app.cli <comando>`, com o mesmo `FACEAI_DATA_DIR` do servidor.

## Cópias de segurança

```bash
docker compose exec faceai python -m app.cli backup /data/backups --files
```

O comando cria, na pasta de destino:
- `faceai_<data>.db`: uma cópia consistente da base de dados (`VACUUM INTO`). Copiar o `faceai.db` à mão com o servidor ligado pode perder as últimas escritas;
- `manifest_<data>.json`: a lista de vídeos e frames guardados com o SHA-256 de cada um, e o SHA-256 da própria cópia da base de dados;
- `secret.key`: a chave que cifra os segredos MFA (permissões 600). Sem ela, quem tem MFA ativo teria de o reconfigurar. Se usar `FACEAI_SECRET_KEY`, guarde essa variável à parte;
- com `--files`: `files/` com os processos (vídeos, recortes, frames) e o `audit.jsonl`. A cópia é **incremental**: só copia ficheiros novos ou alterados.

**Guarde a cópia fora do servidor**: noutro disco ou noutra máquina, cifrada se sair das instalações.

### Backup agendado (exemplo, cron no anfitrião)

```cron
# todos os dias às 02:30
30 2 * * * cd /opt/FaceAI && docker compose exec -T faceai python -m app.cli backup /data/backups --files >> /var/log/faceai-backup.log 2>&1
```

A pasta `/data/backups` do contentor é `./data/backups` no anfitrião. Copie-a depois para o destino final, por exemplo com `rsync`.

## Restaurar

1. Pare o servidor: `docker compose down`.
2. Reponha a pasta de dados:
   - copie `files/cases` para `./data/cases` e `files/audit.jsonl` para `./data/audit.jsonl`;
   - copie a base de dados escolhida (`faceai_<data>.db`) para `./data/faceai.db`. Apague `faceai.db-wal` e `faceai.db-shm`, se existirem;
   - copie `secret.key` para `./data/secret.key`, com permissões 600.
3. Arranque: `docker compose up -d`.
4. Verifique:
   - `docker compose exec faceai python -m app.cli verify-audit` (cadeia de auditoria);
   - `docker compose exec faceai python -m app.cli verify-evidence` (SHA-256 de todos os vídeos).

Se a pasta de dados mudou de sítio, o FaceAI corrige os caminhos no arranque e regista a mudança na auditoria.

## Atualizar

```bash
cd /opt/FaceAI && git pull
docker compose up -d --build
```

A base de dados migra automaticamente: as colunas novas são acrescentadas e nenhum dado é apagado. A análise em curso volta à fila ao parar e recomeça no arranque. As alterações locais ficam no `.env` e num `docker-compose.override.yml`, que não são versionados, por isso o `git pull` não entra em conflito. Mais detalhes em [INSTALACAO.md](INSTALACAO.md#d-atualizar-uma-instalação-existente).

## Consola (`python -m app.cli`)

| Comando | Para quê |
|---|---|
| `create-admin [utilizador]` | cria um administrador (alternativa ao ecrã de configuração inicial no browser) |
| `reset-password <utilizador>` | password temporária; termina as sessões |
| `reset-mfa <utilizador>` | remove o MFA (por exemplo, telemóvel perdido) |
| `unlock <utilizador>` | desbloqueia a conta |
| `list-users` | lista os utilizadores |
| `verify-audit` | verifica a cadeia de hashes da auditoria |
| `verify-evidence [processo]` | recalcula o SHA-256 dos vídeos e compara com o da importação |
| `backup <pasta> [--files]` | cópia de segurança (ver acima) |
| `remove-originals [processo] [--dry-run] [--yes]` | apaga os originais já convertidos e verificados (fica o SHA-256) |
| `prune-frames [processo] [--dry-run]` | apaga os frames anotados gravados por versões anteriores (são gerados a pedido) |
| `sec-info <ficheiro.sec> [--extract destino]` | mostra a estrutura de um `.sec` e, se pedido, extrai o vídeo |

## Saúde, registos e disco

- **Estado:** `GET /api/health` (sem login) verifica a base de dados, a escrita na pasta de dados, o disco, o executor e os modelos. O `docker compose ps` mostra `healthy`/`unhealthy`.
- **Registos:**
  - com Docker, `docker compose logs -f faceai`, com rotação de 5 × 20 MB definida no `docker-compose.yml`;
  - na instalação nativa, `FACEAI_LOG_FILE` (com rotação) e `FACEAI_LOG_FORMAT=json` para ferramentas de recolha.
- **Disco:**
  - a interface avisa aos 80% e aos 90%; em nível crítico, recusa uploads que não cabem e novas análises;
  - em *Utilizadores → Armazenamento* vê-se o que ocupa espaço;
  - no **Resumo** de cada processo, o responsável liberta o que é regenerável (cópias de reprodução, caches, frames antigos). A evidência nunca é tocada.
