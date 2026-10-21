#!/usr/bin/env bash
# Run a producer (the intake or the compiler) on a fresh copy of main, then commit and push what it wrote,
# and confirm the commit is on main before reporting success. Used by the intake and compile workflows.
#
# Usage: tools/store.sh --message MSG --paths "p1 p2 ..." [--comment FILE] -- producer command...
# Exit: the producer's own non-zero code, untouched and uncommitted; 0 when stored, or when main already has
# it; 2 when the result could not be stored (the comment file, if given, then says so).
# The producer may append the paths it wrote to the file named by STORE_MANIFEST; the commit is pushed only
# when every git step succeeded and every listed path is in it.
# Env: STORE_REMOTE (origin), STORE_BRANCH (main), STORE_BACKOFF seconds per attempt (5).
set -u
message=""; paths=""; comment=""
while [ $# -gt 0 ]; do
  case "$1" in
    --message) message=$2; shift 2 ;;
    --paths) paths=$2; shift 2 ;;
    --comment) comment=$2; shift 2 ;;
    --) shift; break ;;
    *) echo "store.sh: unknown option $1" >&2; exit 2 ;;
  esac
done
[ $# -gt 0 ] || { echo "store.sh: producer command required" >&2; exit 2; }
remote=${STORE_REMOTE:-origin}; branch=${STORE_BRANCH:-main}; backoff=${STORE_BACKOFF:-5}
reason="no attempt made"
for attempt in 1 2 3 4 5; do
  if ! git fetch -q "$remote" "$branch" || ! git reset -q --hard "$remote/$branch"; then
    reason="could not sync with $remote/$branch"; sleep $((attempt * backoff)); continue
  fi
  STORE_MANIFEST=$(mktemp); export STORE_MANIFEST
  "$@"; code=$?
  [ "$code" -ne 0 ] && exit "$code"
  staged=1
  for p in $paths; do
    [ -e "$p" ] || continue
    git add -A -- "$p" || { staged=0; break; }
  done
  [ "$staged" -eq 1 ] || { reason="staging failed"; sleep $((attempt * backoff)); continue; }
  if git diff --cached --quiet; then
    if [ -s "$STORE_MANIFEST" ]; then reason="the producer wrote files but nothing was staged"; sleep $((attempt * backoff)); continue; fi
    echo "nothing to store: $remote/$branch already has it"; exit 0
  fi
  if ! git -c user.name="github-actions[bot]" -c user.email="41898282+github-actions[bot]@users.noreply.github.com" commit -q -m "$message"; then
    reason="commit failed"; continue
  fi
  missing=""
  while IFS= read -r written; do
    [ -n "$written" ] || continue
    git cat-file -e "HEAD:$written" 2>/dev/null || missing="$missing $written"
  done < "$STORE_MANIFEST"
  if [ -n "$missing" ]; then reason="the commit lacks files the producer wrote:$missing"; sleep $((attempt * backoff)); continue; fi
  if git push -q "$remote" "HEAD:$branch" && git fetch -q "$remote" "$branch" && git merge-base --is-ancestor HEAD "$remote/$branch"; then
    echo "stored $(git rev-parse --short HEAD) on $remote/$branch"; exit 0
  fi
  reason="push rejected or the commit is not on $remote/$branch"; sleep $((attempt * backoff))
done
echo "store.sh: not stored: $reason" >&2
[ -n "$comment" ] && printf 'The submission was validated but could not be stored (%s). Nothing was stored. Edit the issue to retry.\n' "$reason" > "$comment"
exit 2
