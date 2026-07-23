# Burnless templates

Versioned drop-ins that live in the burnless repo and get installed into other
tools (Claude Code, agents, and so on).

## delegation_filter.sh

A Bash hook that classifies the main session's Bash commands as bronze/silver/gold
and blocks the mechanical ones, pushing them to a cheaper worker instead.
Read-only `ssh`/`sudo` commands pass through: reading output is interpretation
work, and bouncing it through a worker just costs a round trip.

### Install

```bash
mkdir -p ~/.claude/scripts
ln -sf "$(pwd)/templates/delegation_filter.sh" ~/.claude/scripts/delegation_filter.sh
```

Then add a PreToolUse hook for Bash in `~/.claude/settings.json`:

```json
{
  "type": "command",
  "command": "bash ~/.claude/scripts/delegation_filter.sh"
}
```

### Rules

| Category | Example | Decision |
|---|---|---|
| ssh, read-only | `ssh host 'cat /etc/nginx.conf'` | ALLOW |
| ssh running a check | `ssh host 'sudo nginx -t'` | ALLOW |
| ssh, mutating | `ssh host 'sudo systemctl reload nginx'` | BLOCK |
| ssh, interactive | `ssh -t host` | BLOCK |
| sudo, read-only | `sudo cat /etc/sudoers` | ALLOW |
| sudo status query | `sudo systemctl is-active nginx` | ALLOW |
| sudo, mutating | `sudo systemctl restart nginx` | BLOCK |
| rsync / scp / lftp | `rsync -av host:/foo /bar` | BLOCK |
| explicit override | `# OURO_OK <reason>` on the first line | ALLOW |

Blocked means: delegate it instead (`burnless do --tier bronze "..."`).

### Logs

Every decision is appended to `~/.claude/logs/delegation_filter.log`
(timestamp, decision, command snippet).
