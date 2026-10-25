"""Tests for Ticket TKT-02: Portable AGENTS.md, skills, and optional adapters."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def parse_frontmatter(content: str) -> dict[str, Any]:
    """Extract and parse YAML frontmatter from Markdown content."""
    match = re.match(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", content, re.DOTALL)
    if not match:
        raise ValueError("Missing or malformed YAML frontmatter delimiters (---)")
    parsed = yaml.safe_load(match.group(1))
    if not isinstance(parsed, dict):
        raise TypeError("YAML frontmatter must parse to a dictionary")
    return parsed


# ============================================================================
# 1. Root Instructions & 61 Skills Verification
# ============================================================================


def test_root_instructions_exist() -> None:
    """Verify AGENTS.md and CLAUDE.md exist with proper structure."""
    agents_md = REPO_ROOT / "AGENTS.md"
    claude_md = REPO_ROOT / "CLAUDE.md"

    assert agents_md.is_file(), "Root AGENTS.md must exist"
    assert claude_md.is_file(), "Root CLAUDE.md must exist"

    claude_content = claude_md.read_text(encoding="utf-8").strip()
    assert claude_content == "@AGENTS.md", "CLAUDE.md must contain '@AGENTS.md'"


def test_skills_count_and_one_level_depth() -> None:
    """Verify all 61 ml4t skills exist exactly one level deep in .agents/skills/."""
    skills_dir = REPO_ROOT / ".agents" / "skills"
    assert skills_dir.is_dir(), ".agents/skills/ directory must exist"

    skill_dirs = sorted([d for d in skills_dir.iterdir() if d.is_dir() and d.name.startswith("ml4t-")])
    assert len(skill_dirs) == 61, f"Expected exactly 61 ml4t-* skill directories, found {len(skill_dirs)}"

    for skill_dir in skill_dirs:
        subdirs = [p for p in skill_dir.iterdir() if p.is_dir()]
        assert not subdirs, f"Skill directory '{skill_dir.name}' contains nested directories: {subdirs}"


def test_skills_files_and_zero_executables() -> None:
    """Verify skill directories contain zero files except SKILL.md and .ml4t-installed, and no executables."""
    skills_dir = REPO_ROOT / ".agents" / "skills"
    allowed_names = {"SKILL.md", ".ml4t-installed"}

    skill_dirs = [d for d in skills_dir.iterdir() if d.is_dir() and d.name.startswith("ml4t-")]
    assert len(skill_dirs) == 61

    for skill_dir in skill_dirs:
        children = list(skill_dir.iterdir())
        child_names = {c.name for c in children}

        assert child_names.issubset(
            allowed_names
        ), f"Skill '{skill_dir.name}' contains unexpected files: {child_names - allowed_names}"

        skill_md = skill_dir / "SKILL.md"
        assert skill_md.is_file(), f"Skill '{skill_dir.name}' missing SKILL.md"

        for child in children:
            assert (
                child.stat().st_mode & 0o111 == 0
            ), f"File '{child}' in skill '{skill_dir.name}' has executable permissions"


def test_skills_yaml_frontmatter_name_and_description() -> None:
    """Verify all 61 SKILL.md files have valid frontmatter containing name and description."""
    skills_dir = REPO_ROOT / ".agents" / "skills"
    skill_dirs = sorted([d for d in skills_dir.iterdir() if d.is_dir() and d.name.startswith("ml4t-")])

    for skill_dir in skill_dirs:
        skill_md = skill_dir / "SKILL.md"
        content = skill_md.read_text(encoding="utf-8")
        fm = parse_frontmatter(content)

        name = fm.get("name")
        assert isinstance(name, str) and name.strip(), f"Missing or empty 'name' in {skill_md}"
        assert name == skill_dir.name, f"Frontmatter name '{name}' does not match directory '{skill_dir.name}'"

        description = fm.get("description")
        assert isinstance(description, str) and description.strip(), f"Missing or empty 'description' in {skill_md}"


# ============================================================================
# 2. Adapter Templates Validation
# ============================================================================


def test_omp_adapter_templates_valid() -> None:
    """Verify OMP config.yml and subagent definitions under adapters/omp/.omp/."""
    omp_dir = REPO_ROOT / "adapters" / "omp" / ".omp"
    config_yml = omp_dir / "config.yml"
    assert config_yml.is_file(), "adapters/omp/.omp/config.yml must exist"

    config_data = yaml.safe_load(config_yml.read_text(encoding="utf-8"))
    assert isinstance(config_data, dict)

    scout_md = omp_dir / "agents" / "literature-scout.md"
    auditor_md = omp_dir / "agents" / "adversarial-auditor.md"
    assert scout_md.is_file(), "literature-scout.md template must exist"
    assert auditor_md.is_file(), "adversarial-auditor.md template must exist"

    scout_fm = parse_frontmatter(scout_md.read_text(encoding="utf-8"))
    assert scout_fm.get("name") == "literature-scout"
    assert scout_fm.get("blocking") is True
    assert scout_fm.get("structured") is True
    assert isinstance(scout_fm.get("tools"), list)
    assert isinstance(scout_fm.get("autoloadSkills"), list)

    auditor_fm = parse_frontmatter(auditor_md.read_text(encoding="utf-8"))
    assert auditor_fm.get("name") == "adversarial-auditor"
    assert auditor_fm.get("blocking") is True
    assert auditor_fm.get("structured") is True
    assert isinstance(auditor_fm.get("tools"), list)
    assert isinstance(auditor_fm.get("autoloadSkills"), list)



def test_opencode_adapter_templates_valid() -> None:
    """Verify OpenCode adapter configuration and prompt files."""
    opencode_json = REPO_ROOT / "adapters" / "opencode" / "opencode.json"
    assert opencode_json.is_file(), "adapters/opencode/opencode.json must exist"

    data = json.loads(opencode_json.read_text(encoding="utf-8"))
    assert "agent" in data
    agents = data["agent"]
    assert "plan" in agents
    assert "build" in agents
    assert "literature-scout" in agents
    assert "adversarial-auditor" in agents
    assert agents["adversarial-auditor"].get("prompt") == "{file:./.opencode/prompts/adversarial_auditor.txt}"

    prompt_txt = REPO_ROOT / "adapters" / "opencode" / ".opencode" / "prompts" / "adversarial_auditor.txt"
    assert prompt_txt.is_file(), "adapters/opencode/.opencode/prompts/adversarial_auditor.txt must exist"


def test_claude_and_codex_adapter_templates_valid() -> None:
    """Verify Claude Code settings template and Codex documentation."""
    claude_settings = REPO_ROOT / "adapters" / "claude" / "settings.local.json.example"
    assert claude_settings.is_file(), "settings.local.json.example must exist"
    settings_data = json.loads(claude_settings.read_text(encoding="utf-8"))
    deny_list = settings_data.get("permissions", {}).get("deny", [])
    assert any("holdout" in str(item) for item in deny_list)
    assert any("qualify" in str(item) for item in deny_list)

    codex_readme = REPO_ROOT / "adapters" / "codex" / "README.md"
    assert codex_readme.is_file(), "adapters/codex/README.md must exist"


def test_installer_script_executable() -> None:
    """Verify scripts/setup_agent_adapter.sh exists and is executable."""
    installer = REPO_ROOT / "scripts" / "setup_agent_adapter.sh"
    assert installer.is_file(), "scripts/setup_agent_adapter.sh must exist"
    assert installer.stat().st_mode & 0o111 != 0, "scripts/setup_agent_adapter.sh must be executable (chmod +x)"


# ============================================================================
# 3. Installer Tests in Isolated tmp_path Copies
# ============================================================================


@pytest.fixture
def isolated_repo(tmp_path: Path) -> Path:
    """Construct an isolated copy of the repository skeleton for installer testing."""
    repo = tmp_path / "quant-lab-fixture"
    repo.mkdir()

    shutil.copytree(REPO_ROOT / "scripts", repo / "scripts")
    installer = repo / "scripts" / "setup_agent_adapter.sh"
    installer.chmod(0o755)

    shutil.copytree(REPO_ROOT / "adapters", repo / "adapters")
    shutil.copytree(REPO_ROOT / ".agents", repo / ".agents")
    shutil.copy(REPO_ROOT / "AGENTS.md", repo / "AGENTS.md")
    shutil.copy(REPO_ROOT / "CLAUDE.md", repo / "CLAUDE.md")

    return repo


def run_installer(repo: Path, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run setup_agent_adapter.sh with arguments."""
    script = repo / "scripts" / "setup_agent_adapter.sh"
    return subprocess.run(
        [str(script), *args],
        check=False,
        cwd=cwd or repo,
        capture_output=True,
        text=True,
    )


def test_installer_omp_harness(isolated_repo: Path) -> None:
    """Test --harness omp installs symlink to adapters/omp/.omp idempotently."""
    proc1 = run_installer(isolated_repo, "--harness", "omp")
    assert proc1.returncode == 0, f"Installer failed: {proc1.stderr}"

    omp_link = isolated_repo / ".omp"
    assert omp_link.is_symlink()
    assert os.readlink(omp_link) == "adapters/omp/.omp"
    assert (omp_link / "config.yml").is_file()

    proc2 = run_installer(isolated_repo, "--harness", "omp")
    assert proc2.returncode == 0
    assert omp_link.is_symlink()


def test_installer_opencode_harness(isolated_repo: Path) -> None:
    """Test --harness opencode installs opencode.json and .opencode prompts idempotently."""
    proc1 = run_installer(isolated_repo, "--harness", "opencode")
    assert proc1.returncode == 0, f"Installer failed: {proc1.stderr}"

    opencode_json = isolated_repo / "opencode.json"
    assert opencode_json.is_symlink()
    assert os.readlink(opencode_json) == "adapters/opencode/opencode.json"

    prompts_link = isolated_repo / ".opencode" / "prompts"
    assert prompts_link.is_symlink()
    assert (prompts_link / "adversarial_auditor.txt").is_file()

    proc2 = run_installer(isolated_repo, "--harness", "opencode")
    assert proc2.returncode == 0
    assert opencode_json.is_symlink()
    assert prompts_link.is_symlink()


def test_installer_claude_harness(isolated_repo: Path) -> None:
    """Test --harness claude installs .claude/skills and settings.local.json idempotently."""
    proc1 = run_installer(isolated_repo, "--harness", "claude")
    assert proc1.returncode == 0, f"Installer failed: {proc1.stderr}"

    skills_link = isolated_repo / ".claude" / "skills"
    assert skills_link.is_symlink()
    assert (skills_link / "ml4t-purging-embargo" / "SKILL.md").is_file()

    settings_link = isolated_repo / ".claude" / "settings.local.json"
    assert settings_link.is_symlink()
    assert settings_link.is_file()

    proc2 = run_installer(isolated_repo, "--harness", "claude")
    assert proc2.returncode == 0
    assert skills_link.is_symlink()
    assert settings_link.is_symlink()


def test_installer_codex_harness(isolated_repo: Path) -> None:
    """Test --harness codex is a native no-op without symlinks."""
    proc = run_installer(isolated_repo, "--harness", "codex")
    assert proc.returncode == 0
    assert "native" in proc.stdout.lower()
    assert not (isolated_repo / ".omp").exists()
    assert not (isolated_repo / "opencode.json").exists()


def test_installer_all_and_clean_lifecycle(isolated_repo: Path) -> None:
    """Test --harness all sets up all harnesses, and --harness clean removes ONLY exact owned links."""
    proc_all = run_installer(isolated_repo, "--harness", "all")
    assert proc_all.returncode == 0

    assert (isolated_repo / ".omp").is_symlink()
    assert (isolated_repo / "opencode.json").is_symlink()
    assert (isolated_repo / ".opencode" / "prompts").is_symlink()
    assert (isolated_repo / ".claude" / "skills").is_symlink()
    assert (isolated_repo / ".claude" / "settings.local.json").is_symlink()

    proc_clean = run_installer(isolated_repo, "--harness", "clean")
    assert proc_clean.returncode == 0

    assert not (isolated_repo / ".omp").exists()
    assert not (isolated_repo / "opencode.json").exists()
    assert not (isolated_repo / ".opencode" / "prompts").exists()
    assert not (isolated_repo / ".claude" / "skills").exists()
    assert not (isolated_repo / ".claude" / "settings.local.json").exists()

    assert (isolated_repo / "AGENTS.md").is_file()
    assert (isolated_repo / "CLAUDE.md").is_file()
    assert (isolated_repo / ".agents" / "skills").is_dir()
    assert (isolated_repo / ".claude").is_dir()
    assert (isolated_repo / ".opencode").is_dir()

    proc_clean2 = run_installer(isolated_repo, "--harness", "clean")
    assert proc_clean2.returncode == 0


def test_installer_unmanaged_collision_refusal(isolated_repo: Path) -> None:
    """Verify installer refuses overwriting unmanaged files/dirs/symlinks and clean preserves them."""
    unmanaged_omp = isolated_repo / ".omp"
    unmanaged_omp.mkdir()
    (unmanaged_omp / "user_file.txt").write_text("precious user content")

    unmanaged_opencode = isolated_repo / "opencode.json"
    unmanaged_opencode.write_text('{"custom": true}')

    (isolated_repo / ".claude").mkdir(exist_ok=True)
    unmanaged_claude_skills = isolated_repo / ".claude" / "skills"
    unmanaged_claude_skills.symlink_to("/tmp")

    proc_omp = run_installer(isolated_repo, "--harness", "omp")
    assert proc_omp.returncode != 0
    assert "Unmanaged collision" in proc_omp.stderr
    assert unmanaged_omp.is_dir()
    assert (unmanaged_omp / "user_file.txt").read_text() == "precious user content"

    proc_opencode = run_installer(isolated_repo, "--harness", "opencode")
    assert proc_opencode.returncode != 0
    assert "Unmanaged collision" in proc_opencode.stderr
    assert unmanaged_opencode.is_file() and not unmanaged_opencode.is_symlink()

    proc_claude = run_installer(isolated_repo, "--harness", "claude")
    assert proc_claude.returncode != 0
    assert "Unmanaged collision" in proc_claude.stderr
    assert unmanaged_claude_skills.is_symlink()
    assert os.readlink(unmanaged_claude_skills) == "/tmp"

    proc_clean = run_installer(isolated_repo, "--harness", "clean")
    assert proc_clean.returncode == 0
    assert unmanaged_omp.is_dir()
    assert (unmanaged_omp / "user_file.txt").read_text() == "precious user content"
    assert unmanaged_opencode.is_file()
    assert unmanaged_claude_skills.is_symlink()


def test_installer_traversal_and_invalid_args_rejected(isolated_repo: Path) -> None:
    """Verify installer rejects path traversal, unknown arguments, and empty invocations."""
    res_noargs = run_installer(isolated_repo)
    assert res_noargs.returncode != 0

    res_unknown = run_installer(isolated_repo, "--unknown-flag")
    assert res_unknown.returncode != 0

    res_invalid_harness = run_installer(isolated_repo, "--harness", "bogus")
    assert res_invalid_harness.returncode != 0

    res_traversal1 = run_installer(isolated_repo, "--harness", "../omp")
    assert res_traversal1.returncode != 0

    res_traversal2 = run_installer(isolated_repo, "--harness", "../../etc")
    assert res_traversal2.returncode != 0


def test_installer_runs_from_external_cwd(isolated_repo: Path, tmp_path: Path) -> None:
    """Verify setup_agent_adapter.sh operates on its own repo root when executed from an external cwd."""
    external_dir = tmp_path / "somewhere_else"
    external_dir.mkdir()

    res = run_installer(isolated_repo, "--harness", "omp", cwd=external_dir)
    assert res.returncode == 0
    assert (isolated_repo / ".omp").is_symlink()
    assert not (external_dir / ".omp").exists()


def test_installer_clean_exact_string_and_symlinked_parent_preservation(
    isolated_repo: Path, tmp_path: Path
) -> None:
    """Verify clean uses exact readlink string equality, skips symlink parents, and ignores uncreated destinations."""
    # 1. Symlinked parent directory: ensure clean never follows symlink parents to unlink external files
    outside_dir = tmp_path / "outside_claude"
    outside_dir.mkdir()
    outside_target = outside_dir / "skills"
    outside_target.symlink_to("../.agents/skills")
    (isolated_repo / ".claude").symlink_to(outside_dir)

    # 2. Canonical match with non-identical string (e.g. leading ./) must NOT be removed
    (isolated_repo / ".omp").symlink_to("./adapters/omp/.omp")

    # 3. Symlinks at unmanaged destinations (.opencode, root settings.local.json) must NOT be removed
    (isolated_repo / ".opencode").symlink_to("adapters/opencode/.opencode")
    (isolated_repo / "settings.local.json").symlink_to("adapters/claude/settings.local.json.example")

    proc_clean = run_installer(isolated_repo, "--harness", "clean")
    assert proc_clean.returncode == 0

    # Outside target preserved
    assert outside_target.is_symlink()
    # Non-identical string preserved
    assert (isolated_repo / ".omp").is_symlink()
    assert os.readlink(isolated_repo / ".omp") == "./adapters/omp/.omp"
    # Unmanaged destinations preserved
    assert (isolated_repo / ".opencode").is_symlink()
    assert (isolated_repo / "settings.local.json").is_symlink()
