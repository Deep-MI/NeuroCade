#!/usr/bin/env bash
# Purpose:
#   Supports the NeuroCade release compute tag workflow.

set -euo pipefail

kind="${RELEASE_KIND:-}"
date_input="${RELEASE_DATE:-}"
correction="${RELEASE_CORRECTION:-}"

if [[ -z "$kind" ]]; then
  echo "RELEASE_KIND is required: beta or stable" >&2
  exit 1
fi

normalize_date() {
  local value="$1"
  if [[ -z "$value" ]]; then
    date -u '+%Y.%-m.%-d'
    return
  fi
  if [[ "$value" =~ ^([0-9]{4})[-.]0*([1-9][0-9]?)[-.]0*([1-9][0-9]?)$ ]]; then
    printf '%s.%s.%s\n' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}" "${BASH_REMATCH[3]}"
    return
  fi
  echo "Invalid release date: $value. Use YYYY.M.D or YYYY-MM-DD." >&2
  exit 1
}

latest_reachable_release_tag() {
  git describe --tags --match 'v[0-9]*' --abbrev=0 HEAD 2>/dev/null || true
}

next_beta_tag() {
  local release_date="$1"
  local latest=0
  local tag suffix
  while IFS= read -r tag; do
    suffix="${tag##*.}"
    if [[ "$suffix" =~ ^[0-9]+$ ]] && (( suffix > latest )); then
      latest="$suffix"
    fi
  done < <(git tag --list "v${release_date}-beta.*")
  printf 'v%s-beta.%s\n' "$release_date" "$((latest + 1))"
}

stable_tag() {
  local release_date="$1"
  local base="v${release_date}"
  local latest=0
  local tag suffix

  if [[ -n "$correction" ]]; then
    if [[ ! "$correction" =~ ^[0-9]+$ ]]; then
      echo "RELEASE_CORRECTION must be a non-negative integer." >&2
      exit 1
    fi
    if [[ "$correction" == "0" ]]; then
      printf '%s\n' "$base"
    else
      printf '%s-%s\n' "$base" "$correction"
    fi
    return
  fi

  if ! git rev-parse -q --verify "refs/tags/${base}" >/dev/null; then
    printf '%s\n' "$base"
    return
  fi

  while IFS= read -r tag; do
    suffix="${tag##*-}"
    if [[ "$suffix" =~ ^[0-9]+$ ]] && (( suffix > latest )); then
      latest="$suffix"
    fi
  done < <(git tag --list "${base}-[0-9]*")
  printf '%s-%s\n' "$base" "$((latest + 1))"
}

release_date="$(normalize_date "$date_input")"
head_sha="$(git rev-parse HEAD)"
latest_tag="$(latest_reachable_release_tag)"
should_release="true"
prerelease="false"

case "$kind" in
  beta)
    tag="$(next_beta_tag "$release_date")"
    prerelease="true"
    if [[ -n "$latest_tag" ]]; then
      commits_since_latest="$(git rev-list "${latest_tag}..HEAD" --count)"
      if [[ "$commits_since_latest" == "0" ]]; then
        should_release="false"
      fi
    fi
    ;;
  stable)
    tag="$(stable_tag "$release_date")"
    ;;
  *)
    echo "Invalid RELEASE_KIND: $kind. Use beta or stable." >&2
    exit 1
    ;;
esac

if git rev-parse -q --verify "refs/tags/${tag}" >/dev/null; then
  echo "Release tag already exists: $tag" >&2
  exit 1
fi

version="${tag#v}"

{
  echo "tag=$tag"
  echo "version=$version"
  echo "date=$release_date"
  echo "kind=$kind"
  echo "should_release=$should_release"
  echo "prerelease=$prerelease"
  echo "head_sha=$head_sha"
  echo "latest_reachable_release_tag=$latest_tag"
} | tee -a "${GITHUB_OUTPUT:-/dev/null}"

{
  echo "Release tag plan"
  echo "  kind: $kind"
  echo "  tag: $tag"
  echo "  version: $version"
  echo "  head_sha: $head_sha"
  echo "  latest_reachable_release_tag: ${latest_tag:-none}"
  echo "  should_release: $should_release"
} >&2
