#!/usr/bin/env bash
# scripts/setup_agent_adapter.sh
# Idempotently installs or cleans non-load-bearing agent adapter symlinks.
# Supported harnesses: omp | opencode | claude | codex | all | clean

set -euo pipefail

# Ensure the installer always anchors at the repository root containing this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
cd "$REPO_ROOT" || exit 1

usage() {
    cat <<EOF
Usage: $0 --harness <harness>

Supported harnesses:
  omp       Link .omp -> adapters/omp/.omp
  opencode  Link opencode.json and .opencode/prompts
  claude    Link .claude/skills -> .agents/skills and .claude/settings.local.json -> template
  codex     Native AGENTS.md / skills support (no adapter symlinks needed)
  all       Install all adapters idempotently
  clean     Remove only installer-owned adapter symlinks
EOF
}

# 1. Argument validation
if [ "$#" -eq 0 ]; then
    echo "Error: Missing required arguments." >&2
    usage >&2
    exit 1
fi

harness=""

while [ "$#" -gt 0 ]; do
    case "$1" in
        --harness)
            if [ -z "${2:-}" ]; then
                echo "Error: --harness requires a value." >&2
                exit 1
            fi
            harness="$2"
            shift 2
            ;;
        --harness=*)
            harness="${1#*=}"
            shift 1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Error: Unknown argument '$1'." >&2
            exit 1
            ;;
    esac
done

# Refuse path traversal and invalid characters
if [[ "$harness" == *".."* ]] || [[ "$harness" == *"/"* ]] || [[ "$harness" == *"\\"* ]]; then
    echo "Error: Path traversal or invalid character detected in harness: '$harness'" >&2
    exit 1
fi

case "$harness" in
    omp|opencode|claude|codex|all|clean)
        ;;
    *)
        echo "Error: Invalid harness '$harness'. Allowed: omp|opencode|claude|codex|all|clean" >&2
        exit 1
        ;;
esac

# 2. Managed symlink helpers

ensure_symlink() {
    local dest="$1"
    local target="$2"

    # Refuse traversal or absolute destinations
    if [[ "$dest" == *".."* ]] || [[ "$dest" == /* ]]; then
        echo "Error: Path traversal or absolute destination rejected: '$dest'" >&2
        exit 1
    fi

    local parent
    parent="$(dirname "$dest")"
    if [ "$parent" != "." ]; then
        if [ -L "$parent" ]; then
            echo "Error: Unmanaged collision: parent directory '$parent' is a symlink" >&2
            exit 1
        elif [ -e "$parent" ] && [ ! -d "$parent" ]; then
            echo "Error: Unmanaged collision: parent '$parent' exists and is not a directory" >&2
            exit 1
        fi
        mkdir -p "$parent"
    fi

    if [ -L "$dest" ]; then
        local current_target
        current_target="$(readlink "$dest")"
        if [ "$current_target" = "$target" ]; then
            echo "Already installed (idempotent): $dest -> $target"
            return 0
        fi
        echo "Error: Unmanaged collision at '$dest': symlink points to '$current_target' (expected '$target')" >&2
        exit 1
    elif [ -e "$dest" ]; then
        echo "Error: Unmanaged collision at '$dest': existing regular file or directory is not an installer symlink" >&2
        exit 1
    else
        ln -s "$target" "$dest"
        echo "Linked: $dest -> $target"
    fi
}

clean_symlink() {
    local dest="$1"
    local expected_target="$2"

    local parent
    parent="$(dirname "$dest")"
    if [ "$parent" != "." ]; then
        # Reject and skip unlinking into symlinked parent directories
        if [ -L "$parent" ]; then
            echo "Preserving symlinked parent directory: $parent (skipping $dest)"
            return 0
        fi
        if [ ! -d "$parent" ]; then
            return 0
        fi
    fi

    if [ ! -L "$dest" ]; then
        # Never remove real files or directories
        return 0
    fi

    local current_target
    current_target="$(readlink "$dest")"
    # Strict exact readlink string equality required
    if [ "$current_target" = "$expected_target" ]; then
        rm -f "$dest"
        echo "Cleaned symlink: $dest"
        return 0
    fi

    echo "Preserving unmanaged symlink: $dest -> $current_target"
    return 0
}

# 3. Harness Actions

setup_omp() {
    if [ ! -d "adapters/omp/.omp" ]; then
        echo "Error: Adapter template source 'adapters/omp/.omp' does not exist." >&2
        exit 1
    fi
    ensure_symlink ".omp" "adapters/omp/.omp"
}

setup_opencode() {
    if [ ! -f "adapters/opencode/opencode.json" ]; then
        echo "Error: Adapter template source 'adapters/opencode/opencode.json' does not exist." >&2
        exit 1
    fi
    if [ ! -d "adapters/opencode/.opencode/prompts" ]; then
        echo "Error: Adapter template source 'adapters/opencode/.opencode/prompts' does not exist." >&2
        exit 1
    fi
    ensure_symlink "opencode.json" "adapters/opencode/opencode.json"
    ensure_symlink ".opencode/prompts" "../adapters/opencode/.opencode/prompts"
}

setup_claude() {
    if [ ! -f "adapters/claude/settings.local.json.example" ]; then
        echo "Error: Adapter template source 'adapters/claude/settings.local.json.example' does not exist." >&2
        exit 1
    fi
    ensure_symlink ".claude/skills" "../.agents/skills"
    ensure_symlink ".claude/settings.local.json" "../adapters/claude/settings.local.json.example"
}

setup_codex() {
    echo "Codex uses native AGENTS.md and .agents/skills; no adapter symlinks required."
}

clean_adapters() {
    # Remove ONLY exact installer-owned symlinks across the five managed destinations;
    # never real files/dirs, AGENTS.md, skills, or unmanaged parents/destinations
    clean_symlink ".omp" "adapters/omp/.omp"
    clean_symlink "opencode.json" "adapters/opencode/opencode.json"
    clean_symlink ".opencode/prompts" "../adapters/opencode/.opencode/prompts"
    clean_symlink ".claude/skills" "../.agents/skills"
    clean_symlink ".claude/settings.local.json" "../adapters/claude/settings.local.json.example"
    echo "Clean complete: active adapter symlinks removed without touching core instructions or skills."
}

case "$harness" in
    omp)
        setup_omp
        ;;
    opencode)
        setup_opencode
        ;;
    claude)
        setup_claude
        ;;
    codex)
        setup_codex
        ;;
    all)
        setup_omp
        setup_opencode
        setup_claude
        setup_codex
        ;;
    clean)
        clean_adapters
        ;;
esac
