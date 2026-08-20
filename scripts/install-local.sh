#!/bin/sh
set -eu

usage() {
  echo "Usage: $0 {codex|claude-code|all} [skill-name|all]" >&2
  exit 2
}

install_one() {
  agent_name=$1
  target_root=$2
  skill_dir=$3
  skill_name=$(basename "$skill_dir")
  link_path="$target_root/$skill_name"

  if [ -L "$link_path" ] && [ "$(readlink "$link_path")" = "$skill_dir" ]; then
    echo "$agent_name: already installed $skill_name"
    return
  fi
  if [ -e "$link_path" ] || [ -L "$link_path" ]; then
    echo "$agent_name: refusing to replace existing $link_path" >&2
    exit 1
  fi

  ln -s "$skill_dir" "$link_path"
  echo "$agent_name: installed $skill_name -> $link_path"
}

install_for() {
  agent_name=$1
  target_root=$2

  mkdir -p "$target_root"
  if [ "$requested_skill" = all ]; then
    for skill_dir in "$repo_root"/skills/*; do
      [ -f "$skill_dir/SKILL.md" ] || continue
      install_one "$agent_name" "$target_root" "$skill_dir"
    done
  else
    skill_dir="$repo_root/skills/$requested_skill"
    if [ ! -f "$skill_dir/SKILL.md" ]; then
      echo "Unknown skill: $requested_skill" >&2
      exit 2
    fi
    install_one "$agent_name" "$target_root" "$skill_dir"
  fi
}

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
repo_root=$(CDPATH= cd -- "$script_dir/.." && pwd -P)
[ "$#" -le 2 ] || usage
requested_agent=${1:-all}
requested_skill=${2:-all}

case "$requested_skill" in
  all) ;;
  ''|*[!a-z0-9-]*) usage ;;
esac

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
