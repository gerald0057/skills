#!/usr/bin/env python3
"""Validate the repository's portable Agent Skill and plugin structure."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SKILLS_ROOT = ROOT / "skills"
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def parse_frontmatter(path: Path, errors: list[str]) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        fail(errors, f"{path.relative_to(ROOT)}: missing opening frontmatter marker")
        return {}
    end = text.find("\n---\n", 4)
    if end < 0:
        fail(errors, f"{path.relative_to(ROOT)}: missing closing frontmatter marker")
        return {}

    values: dict[str, str] = {}
    for line in text[4:end].splitlines():
        match = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if match:
            values[match.group(1)] = match.group(2).strip().strip('"\'')
    return values


def validate_skill(skill_dir: Path, errors: list[str]) -> None:
    relative = skill_dir.relative_to(ROOT)
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        fail(errors, f"{relative}: missing SKILL.md")
        return

    metadata = parse_frontmatter(skill_md, errors)
    name = metadata.get("name", "")
    description = metadata.get("description", "")
    if name != skill_dir.name:
        fail(errors, f"{relative}: frontmatter name must match directory name")
    if not NAME_RE.fullmatch(name) or len(name) > 64:
        fail(errors, f"{relative}: invalid skill name {name!r}")
    if not description or len(description) > 1024:
        fail(errors, f"{relative}: description must contain 1-1024 characters")
    if set(metadata) != {"name", "description"}:
        fail(errors, f"{relative}: frontmatter must contain only name and description")

    text = skill_md.read_text(encoding="utf-8")
    for raw_target in LINK_RE.findall(text):
        target = raw_target.split("#", 1)[0]
        if not target or "://" in target or target.startswith("mailto:"):
            continue
        if not (skill_dir / target).exists():
            fail(errors, f"{relative}: broken relative link {raw_target!r}")

    agent_yaml = skill_dir / "agents" / "openai.yaml"
    if agent_yaml.is_file():
        agent_text = agent_yaml.read_text(encoding="utf-8")
        for required in ("display_name:", "short_description:", "default_prompt:"):
            if required not in agent_text:
                fail(errors, f"{agent_yaml.relative_to(ROOT)}: missing {required[:-1]}")
        if f"${name}" not in agent_text:
            fail(errors, f"{agent_yaml.relative_to(ROOT)}: default_prompt must mention ${name}")


def load_json(path: Path, errors: list[str]) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(errors, f"{path.relative_to(ROOT)}: missing file")
        return {}
    except json.JSONDecodeError as exc:
        fail(errors, f"{path.relative_to(ROOT)}: invalid JSON: {exc}")
        return {}
    if not isinstance(payload, dict):
        fail(errors, f"{path.relative_to(ROOT)}: root must be an object")
        return {}
    return payload


def validate_manifests(errors: list[str]) -> None:
    codex = load_json(ROOT / ".codex-plugin" / "plugin.json", errors)
    claude = load_json(ROOT / ".claude-plugin" / "plugin.json", errors)
    marketplace = load_json(ROOT / ".claude-plugin" / "marketplace.json", errors)

    if codex.get("name") != "smartrf-skills" or claude.get("name") != "smartrf-skills":
        fail(errors, "Codex and Claude plugin names must both be smartrf-skills")
    if codex.get("version") != claude.get("version"):
        fail(errors, "Codex and Claude plugin versions must match")
    if codex.get("skills") != "./skills/" or claude.get("skills") != "./skills/":
        fail(errors, "plugin manifests must use ./skills/")
    if codex.get("license") != "MIT" or claude.get("license") != "MIT":
        fail(errors, "plugin manifests must declare the repository MIT license")

    plugins = marketplace.get("plugins")
    if not isinstance(plugins, list) or not any(
        isinstance(item, dict)
        and item.get("name") == "smartrf-skills"
        and item.get("source") == "./"
        for item in plugins
    ):
        fail(errors, ".claude-plugin/marketplace.json must publish smartrf-skills from ./")


def main() -> int:
    errors: list[str] = []
    if not SKILLS_ROOT.is_dir():
        fail(errors, "missing skills/ directory")
    else:
        skill_dirs = sorted(path for path in SKILLS_ROOT.iterdir() if path.is_dir())
        if not skill_dirs:
            fail(errors, "skills/ contains no skills")
        for skill_dir in skill_dirs:
            validate_skill(skill_dir, errors)

    validate_manifests(errors)
    if not (ROOT / "LICENSE").is_file():
        fail(errors, "missing LICENSE")

    if errors:
        print("Repository validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Repository validation passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
