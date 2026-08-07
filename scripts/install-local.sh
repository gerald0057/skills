#!/bin/sh
set -eu

usage() {
  echo "Usage: $0 {codex|claude-code|all}" >&2
  exit 2
}

install_for() {
  agent_name=$1
  target_root=$2

  mkdir -p "$target_root"
  for skill_dir in "$repo_root"/skills/*; do
    [ -f "$skill_dir/SKILL.md" ] || continue
    skill_name=$(basename "$skill_dir")
    link_path="$target_root/$skill_name"

    if [ -L "$link_path" ] && [ "$(readlink "$link_path")" = "$skill_dir" ]; then
      echo "$agent_name: already installed $skill_name"
      continue
    fi
    if [ -e "$link_path" ] || [ -L "$link_path" ]; then
      echo "$agent_name: refusing to replace existing $link_path" >&2
      exit 1
    fi

    ln -s "$skill_dir" "$link_path"
    echo "$agent_name: installed $skill_name -> $link_path"
  done
}

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
repo_root=$(CDPATH= cd -- "$script_dir/.." && pwd -P)
requested_agent=${1:-all}

case "$requested_agent" in
  codex)
    install_for codex "${HOME}/.agents/skills"
    ;;
  claude-code)
    install_for claude-code "${HOME}/.claude/skills"
    ;;
  all)
    install_for codex "${HOME}/.agents/skills"
    install_for claude-code "${HOME}/.claude/skills"
    ;;
  *)
    usage
    ;;
esac
