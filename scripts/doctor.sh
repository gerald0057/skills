#!/bin/sh
set -u

minimum_gh_version=2.90.0
pass_count=0
warn_count=0
fail_count=0

usage() {
  echo "Usage: $0" >&2
  exit 2
}

pass() {
  pass_count=$((pass_count + 1))
  printf '[PASS] %s\n' "$1"
}

warn() {
  warn_count=$((warn_count + 1))
  printf '[WARN] %s\n' "$1"
}

fail() {
  fail_count=$((fail_count + 1))
  printf '[FAIL] %s\n' "$1"
}

first_line() {
  sed -n '1p'
}

last_nonempty_line() {
  awk 'NF { line = $0 } END { print line }'
}

version_at_least() {
  awk -v actual="$1" -v required="$2" 'BEGIN {
    split(actual, a, /[^0-9]+/)
    split(required, r, /[^0-9]+/)
    for (i = 1; i <= 3; i++) {
      av = a[i] + 0
      rv = r[i] + 0
      if (av > rv) exit 0
      if (av < rv) exit 1
    }
    exit 0
  }'
}

if [ "$#" -ne 0 ]; then
  usage
fi

printf 'Local environment doctor\n\n'

if command -v git >/dev/null 2>&1; then
  git_path=$(command -v git)
  if git_output=$(git --version 2>&1); then
    git_version=$(printf '%s\n' "$git_output" | first_line)
    pass "git: $git_version ($git_path)"
  else
    fail "git: found at $git_path but version check failed"
  fi
else
  fail "git: not found"
fi

if command -v gh >/dev/null 2>&1; then
  gh_path=$(command -v gh)
  if gh_output=$(gh --version 2>&1); then
    gh_line=$(printf '%s\n' "$gh_output" | first_line)
    gh_version=$(printf '%s\n' "$gh_line" | awk '{
      for (i = 1; i <= NF; i++) {
        if ($i ~ /^[0-9]+\.[0-9]+\.[0-9]+/) {
          print $i
          exit
        }
      }
    }')

    if [ -z "$gh_version" ]; then
      fail "gh: unable to determine version ($gh_path)"
    elif version_at_least "$gh_version" "$minimum_gh_version"; then
      pass "gh: $gh_version ($gh_path)"
    else
      fail "gh: $gh_version is older than required $minimum_gh_version ($gh_path)"
    fi
  else
    fail "gh: found at $gh_path but version check failed"
  fi

  if gh skill install --help >/dev/null 2>&1; then
    pass "gh skill: install command is available"
  else
    fail "gh skill: install command is unavailable; upgrade GitHub CLI"
  fi
else
  fail "gh: not found; GitHub CLI $minimum_gh_version or newer is required"
fi

python_command=
if command -v python3 >/dev/null 2>&1; then
  python_command=python3
elif command -v python >/dev/null 2>&1; then
  python_command=python
fi

if [ -n "$python_command" ]; then
  python_path=$(command -v "$python_command")
  if python_output=$("$python_command" --version 2>&1); then
    python_line=$(printf '%s\n' "$python_output" | first_line)
    python_major=$(printf '%s\n' "$python_line" | awk '{
      for (i = 1; i <= NF; i++) {
        if ($i ~ /^[0-9]+\./) {
          split($i, version, ".")
          print version[1]
          exit
        }
      }
    }')
    if [ "$python_major" = "3" ]; then
      pass "python: $python_line ($python_path)"
    else
      fail "python: Python 3 is required; found $python_line ($python_path)"
    fi
  else
    fail "python: found at $python_path but version check failed"
  fi
else
  fail "python: Python 3 not found"
fi

if command -v ssh >/dev/null 2>&1; then
  ssh_path=$(command -v ssh)
  if ssh_output=$(ssh -V 2>&1); then
    ssh_version=$(printf '%s\n' "$ssh_output" | first_line)
    pass "ssh: $ssh_version ($ssh_path)"
  else
    warn "ssh: found at $ssh_path but version check failed"
  fi
else
  warn "ssh: not found; required only for SSH Git repositories"
fi

if command -v codex >/dev/null 2>&1; then
  codex_path=$(command -v codex)
  if codex_output=$(codex --version 2>&1); then
    codex_version=$(printf '%s\n' "$codex_output" | last_nonempty_line)
    pass "codex: $codex_version ($codex_path)"
  else
    warn "codex: found at $codex_path but version check failed"
  fi
else
  warn "codex: not found"
fi

if command -v claude >/dev/null 2>&1; then
  claude_path=$(command -v claude)
  if claude_output=$(claude --version 2>&1); then
    claude_version=$(printf '%s\n' "$claude_output" | last_nonempty_line)
    pass "claude: $claude_version ($claude_path)"
  else
    warn "claude: found at $claude_path but version check failed"
  fi
else
  warn "claude: not found"
fi

printf '\nSummary: %d passed, %d warnings, %d failures.\n' \
  "$pass_count" "$warn_count" "$fail_count"

if [ "$fail_count" -gt 0 ]; then
  exit 1
fi

exit 0
