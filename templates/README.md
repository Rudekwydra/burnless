# Burnless templates

Drop-ins versionados que vivem no repo do burnless e são usados em outras
ferramentas (Claude Code, agents, etc).

## delegation_filter.sh

Hook bash que classifica comandos do main Claude (Opus) como Bronze/Prata/Ouro
e bloqueia tarefas mecânicas, forçando delegação via Agent tool para Haiku/Sonnet.
Comandos SSH/sudo **read-only** passam (interpretação é trabalho Opus, economiza
round-trip via Haiku).

### Install

```bash
mkdir -p ~/.claude/scripts
ln -sf ~/antigravity/burnless/templates/delegation_filter.sh ~/.claude/scripts/delegation_filter.sh
```

Em `~/.claude/settings.json`, adicionar PreToolUse hook para Bash:

```json
{
  "type": "command",
  "command": "bash ~/.claude/scripts/delegation_filter.sh"
}
```

### Tests

```bash
bash /tmp/test_filter.sh  # ver suite em ../tests/ depois (TODO move)
```

### Regras

| Categoria | Exemplo | Decisão |
|---|---|---|
| SSH read-only | `ssh host 'cat /etc/nginx.conf'` | ALLOW |
| SSH com nginx -t | `ssh host 'sudo nginx -t'` | ALLOW |
| SSH mutativo | `ssh host 'sudo systemctl reload nginx'` | BLOCK |
| SSH interativo | `ssh -t host` | BLOCK |
| sudo read-only | `sudo cat /etc/sudoers` | ALLOW |
| sudo systemctl status | `sudo systemctl is-active nginx` | ALLOW |
| sudo mutativo | `sudo systemctl restart nginx` | BLOCK |
| rsync/scp/lftp | `rsync -av host:/foo /bar` | BLOCK |
| Override Opus | `# OURO_OK <razão>\n<cmd>` (1ª linha) | ALLOW |

Bloqueado → delegar via `Agent` tool (haiku=bronze, sonnet=prata/ouro).

### Logs

Toda decisão fica em `~/.claude/logs/delegation_filter.log` (timestamp + decisão + snippet).
