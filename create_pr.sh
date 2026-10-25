#!/usr/bin/env bash
# ==============================================================================
# Quant-Lab Initial Pull Request Automation Script
# Target Repository: https://github.com/hzminhzz/quant-lab
# Branch: feat/initial-architecture-and-plan -> main
# ==============================================================================
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/hzminhzz/quant-lab.git}"
BRANCH_NAME="feat/initial-architecture-and-plan"
PR_TITLE="chore(arch): initialize quant-lab architecture RFC, agent-independent implementation plan, and vertical slice"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="$(mktemp -d -t quant-lab-pr-XXXXXX)"

echo "==> [1/6] Preparing workspace in ${WORK_DIR}..."
trap 'rm -rf "${WORK_DIR}"' EXIT

echo "==> [2/6] Cloning ${REPO_URL}..."
if git clone "${REPO_URL}" "${WORK_DIR}/quant-lab"; then
    cd "${WORK_DIR}/quant-lab"
    # Handle both empty newly-created repos and repos with an initial main branch
    if git rev-parse --verify HEAD >/dev/null 2>&1; then
        git checkout -b "${BRANCH_NAME}"
    else
        echo "==> Repository is empty. Initializing default 'main' commit first so a PR can be opened..."
        git checkout -b main
        cat << 'EOF' > .gitignore
__pycache__/
*.py[cod]
*$py.class
.venv/
venv/
.env
.pytest_cache/
.ruff_cache/
*.db
*.db-shm
*.db-wal
artifacts/
EOF
        git add .gitignore
        git commit -m "chore: initial repository root with .gitignore"
        git push -u origin main
        git checkout -b "${BRANCH_NAME}"
    fi
else
    echo "ERROR: Could not clone ${REPO_URL}. Check your GitHub credentials or SSH/HTTPS access."
    exit 1
fi

echo "==> [3/6] Copying Quant-Lab architecture & implementation plan artifacts..."
REQUIRED_FILES=(
    "README.md"
    "PULL_REQUEST.md"
    "agent_native_quant_research_rfc.md"
    "IMPLEMENTATION_PLAN.md"
    "TICKETS.md"
    "AGENT_COMPATIBILITY.md"
    "AGENT_EXECUTION.md"
    "run_vertical_slice.py"
)

for file in "${REQUIRED_FILES[@]}"; do
    if [[ ! -f "${SCRIPT_DIR}/${file}" ]]; then
        echo "ERROR: Missing required file ${SCRIPT_DIR}/${file}"
        exit 1
    fi
    cp "${SCRIPT_DIR}/${file}" "${WORK_DIR}/quant-lab/${file}"
done

chmod +x "${WORK_DIR}/quant-lab/run_vertical_slice.py"

cat << 'EOF' > "${WORK_DIR}/quant-lab/.gitignore"
__pycache__/
*.py[cod]
*$py.class
.venv/
venv/
.env
.pytest_cache/
.ruff_cache/
*.db
*.db-shm
*.db-wal
artifacts/
EOF

echo "==> [4/6] Staging and committing files on branch '${BRANCH_NAME}'..."
git add .gitignore "${REQUIRED_FILES[@]}"
git commit -m "${PR_TITLE}" -m "Includes:
- agent_native_quant_research_rfc.md (RFC-2026-Q4-001)
- IMPLEMENTATION_PLAN.md (5-day VPS roadmap & DAG)
- TICKETS.md (TKT-01 through TKT-12 bounded tickets)
- AGENT_COMPATIBILITY.md (OMP, OpenCode, Claude Code, Codex CLI, Herdr)
- AGENT_EXECUTION.md (Universal 10-rule single-ticket execution contract)
- run_vertical_slice.py (Verified end-to-end reference slice)
- README.md & PULL_REQUEST.md"

echo "==> [5/6] Pushing branch '${BRANCH_NAME}' to origin..."
git push -u origin "${BRANCH_NAME}"

echo "==> [6/6] Creating Pull Request on GitHub..."
if command -v gh >/dev/null 2>&1; then
    gh pr create \
        --repo hzminhzz/quant-lab \
        --base main \
        --head "${BRANCH_NAME}" \
        --title "${PR_TITLE}" \
        --body-file "${WORK_DIR}/quant-lab/PULL_REQUEST.md"
    echo "==> Pull Request successfully created!"
else
    echo "==> Branch pushed! GitHub CLI ('gh') not found; open your PR directly at:"
    echo "    https://github.com/hzminhzz/quant-lab/compare/main...${BRANCH_NAME}?expand=1"
fi
