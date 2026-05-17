# CLAUDE.md

Working agreement for Claude Code on this repository.

## Environment

- Server: Dell R730xd, 2x V100 16GB, Ubuntu
- Project: EHC AI Helpdesk (on-premise RAG chatbot, Vietnamese)
- Repo path: `/home/phungkien/EHC_HELPDESK/ehc-helpdesk`
- Remote: `git@github.com:phungkien402/EHC_HELPDESK.git` (SSH)

## Commands

**The shell environment lacks PATH — only `/bin/bash` works. Use the patterns below.**

```bash
# --- Shell/system commands (git, ls, find, etc.) ---
/bin/bash -c "export PATH=/home/phungkien/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin && <command>"

# Examples:
/bin/bash -c "export PATH=/home/phungkien/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin && git status"
/bin/bash -c "export PATH=/home/phungkien/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin && ls -la"

# --- Python commands ---
# Use run.sh (sets PATH + cd into project + runs python3):
/bin/bash /home/phungkien/EHC_HELPDESK/ehc-helpdesk/run.sh -m <module>

# Examples:
/bin/bash /home/phungkien/EHC_HELPDESK/ehc-helpdesk/run.sh -m core.pipeline
/bin/bash /home/phungkien/EHC_HELPDESK/ehc-helpdesk/run.sh -m tests.evaluate
/bin/bash /home/phungkien/EHC_HELPDESK/ehc-helpdesk/run.sh -m tests.debug_query "your question"
/bin/bash /home/phungkien/EHC_HELPDESK/ehc-helpdesk/run.sh -m uvicorn api.routes:app --host 0.0.0.0 --port 8080
```

## Git Workflow

```bash
# Always run git from the repo directory:
/bin/bash -c "export PATH=/home/phungkien/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin && cd /home/phungkien/EHC_HELPDESK/ehc-helpdesk && git add -A && git status"
/bin/bash -c "export PATH=/home/phungkien/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin && cd /home/phungkien/EHC_HELPDESK/ehc-helpdesk && git commit -m 'your message'"
/bin/bash -c "export PATH=/home/phungkien/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin && cd /home/phungkien/EHC_HELPDESK/ehc-helpdesk && git push origin main"
```

### Branching Rules

- For new features or experimental changes, always create a new branch first:
  `git checkout -b feature/<short-description>`
- Only merge to main after the reviewer (via Cowork) approves
- Branch naming convention: `feature/<name>`, `fix/<name>`, `experiment/<name>`
- Never commit experimental or untested changes directly to main

### Commit Convention

- `feat:` — new feature
- `fix:` — bug fix
- `docs:` — documentation only
- `refactor:` — code change that neither fixes a bug nor adds a feature

## Review Workflow

1. Implement the change
2. Commit to appropriate branch
3. Push and create PR if on feature branch
4. Reviewer (via Cowork) reviews
5. Fix any issues raised
6. Merge to main only after approval
