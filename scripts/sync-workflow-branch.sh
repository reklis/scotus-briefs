#!/usr/bin/env bash
# Synchronize before work, or prove that a built tree is still current.
set -euo pipefail

mode=${1:---ff-only}
case "$mode" in
  --ff-only|--check|--check-clean) ;;
  *) echo "usage: $0 --ff-only|--check|--check-clean" >&2; exit 2 ;;
esac

branch=$(git symbolic-ref --quiet --short HEAD) || {
  echo "Refusing to synchronize a detached HEAD." >&2
  exit 1
}
if [[ -n ${GITHUB_REF_NAME:-} && $branch != "$GITHUB_REF_NAME" ]]; then
  echo "Checkout branch '$branch' does not match expected branch '$GITHUB_REF_NAME'." >&2
  exit 1
fi

git_auth=()
if [[ -n ${GITHUB_TOKEN:-} ]]; then
  basic_auth=$(printf 'x-access-token:%s' "$GITHUB_TOKEN" | base64 | tr -d '\n')
  git_auth=(-c "http.https://github.com/.extraheader=AUTHORIZATION: basic $basic_auth")
fi

git "${git_auth[@]}" fetch --no-tags origin "$branch"
remote_sha=$(git rev-parse FETCH_HEAD)
local_sha=$(git rev-parse HEAD)

if [[ $mode == --check || $mode == --check-clean ]]; then
  if [[ $local_sha != "$remote_sha" ]]; then
    echo "Default branch moved from $local_sha to $remote_sha; refusing to publish a stale or unvalidated tree." >&2
    exit 1
  fi
  if [[ $mode == --check-clean ]] && [[ -n $(git status --porcelain --untracked-files=normal) ]]; then
    echo "Checkout contains uncommitted files; refusing to publish a build that differs from $local_sha." >&2
    git status --short >&2
    exit 1
  fi
  echo "Checkout is current at $local_sha."
  exit 0
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Refusing to synchronize a checkout with tracked changes." >&2
  exit 1
fi
git merge --ff-only "$remote_sha"
echo "Synchronized $branch at $(git rev-parse HEAD)."
