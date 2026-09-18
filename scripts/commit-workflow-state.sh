#!/usr/bin/env bash
# Commit one workflow stage without sweeping unrelated checkout changes into Git.
set -euo pipefail

usage() {
  echo "usage: $0 source|generated [commit message]" >&2
  exit 2
}

stage=${1:-}
message=${2:-}
case "$stage" in
  source)
    paths=(documents manifests data/cases data/checkpoints reports)
    default_message="chore(archive): preserve workflow source updates"
    ;;
  generated)
    paths=(data/evidence data/guides data/checkpoints reports)
    default_message="chore(guides): publish accepted workflow updates"
    ;;
  *) usage ;;
esac

message=${message:-$default_message}

if [[ -z ${GITHUB_ACTIONS:-} && ${ALLOW_LOCAL_WORKFLOW_COMMIT:-0} != 1 ]]; then
  echo "Refusing to commit outside GitHub Actions. Set ALLOW_LOCAL_WORKFLOW_COMMIT=1 for an intentional local run." >&2
  exit 2
fi

branch=$(git symbolic-ref --quiet --short HEAD) || {
  echo "Refusing to commit from a detached HEAD." >&2
  exit 1
}
expected_branch=${GITHUB_REF_NAME:-$branch}
if [[ $branch != "$expected_branch" ]]; then
  echo "Checkout branch '$branch' does not match expected branch '$expected_branch'." >&2
  exit 1
fi

# -A records intentional corrections/deletions inside these owned data paths, but
# never stages application or workflow source files.
git add -A -- "${paths[@]}"

# A no-change run must still prove it is current before later generation or
# publication. When state did change, commit it first so a rejected concurrent
# push can be retained as a Git bundle rather than losing archived sources.
if git diff --cached --quiet; then
  scripts/sync-workflow-branch.sh --check
  echo "No $stage state changes to commit; checkout remains current."
  exit 0
fi

# Archived blobs are immutable. They may be added, but a workflow may not
# modify, rename, or delete a path already accepted under documents/.
if git diff --cached --quiet --diff-filter=DMRT -- documents; then
  :
else
  echo "Refusing to modify, rename, or delete an archived document:" >&2
  git diff --cached --name-status --diff-filter=DMRT -- documents >&2
  exit 1
fi

# Archived blobs must stay under the content-addressed archive. Do not allow a
# workflow to accidentally stage a PDF elsewhere in the repository.
while IFS= read -r -d '' path; do
  if [[ $path == *.[Pp][Dd][Ff] && $path != documents/* ]]; then
    echo "Refusing to commit PDF outside documents/: $path" >&2
    exit 1
  fi
done < <(git diff --cached --name-only -z --diff-filter=ACMR)

# A failed generator must never remove the last accepted guide.
if [[ $stage == generated ]] && ! git diff --cached --quiet --diff-filter=D -- data/guides; then
  echo "Refusing to delete an accepted guide:" >&2
  git diff --cached --name-status --diff-filter=D -- data/guides >&2
  exit 1
fi

git config user.name "scotus-guide automation"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git commit -m "$message"

# The checkout does not persist credentials while dependencies execute; inject
# the short-lived Actions token only for the push. Never rebase after validation:
# a concurrent update makes this push fail rather than publishing an unvalidated
# merge. Preserve an unpushed commit as a retained workflow artifact for recovery.
git_auth=()
if [[ -n ${GITHUB_TOKEN:-} ]]; then
  basic_auth=$(printf 'x-access-token:%s' "$GITHUB_TOKEN" | base64 | tr -d '\n')
  git_auth=(-c "http.https://github.com/.extraheader=AUTHORIZATION: basic $basic_auth")
fi
if ! git "${git_auth[@]}" push origin "HEAD:$branch"; then
  mkdir -p reports/workflow
  git bundle create "reports/workflow/unpushed-$stage.bundle" "origin/$branch..HEAD" || true
  echo "Push failed; any unpushed commit was saved to reports/workflow/unpushed-$stage.bundle." >&2
  exit 1
fi
echo "Pushed $stage state at $(git rev-parse HEAD)."
