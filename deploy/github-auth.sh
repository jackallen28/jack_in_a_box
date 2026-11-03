#!/usr/bin/env bash
# Sourced by install.sh.  Makes sure git can reach the (private) repo without asking again, so the
# hourly auto-update works.  The token is kept in ONE file, readable only by you:  data/.git-credentials
#
#   setup_github_auth <repo-root> <credentials-file>
#
# Ways the token can arrive (all end up in that file; the clone's remote URL is cleaned afterwards):
#   * pasted inside the clone URL:  https://USER:TOKEN@github.com/owner/repo.git   (README "paste once" snippet)
#   * already stored by git from an earlier prompt
#   * typed when this function asks for it
# Public repos need no token and nothing is stored.

_jiab_verify() { GIT_TERMINAL_PROMPT=0 git -C "$1" ls-remote --exit-code -q origin HEAD >/dev/null 2>&1; }

_jiab_store() {  # _jiab_store repo cred host user token
  printf 'protocol=https\nhost=%s\nusername=%s\npassword=%s\n\n' "$3" "$4" "$5" | git -C "$1" credential approve
  chmod 600 "$2"
}

setup_github_auth() {
  local repo="$1" cred="$2" origin host path user="" token=""
  origin="$(git -C "$repo" remote get-url origin 2>/dev/null)" || { echo "   (not a git clone: auto-update unavailable)"; return 0; }
  [[ "$origin" == https://* ]] || { echo "   (remote isn't https: assuming your SSH key handles access)"; return 0; }

  # 1. token pasted into the URL -> move it out of .git/config
  if [[ "$origin" =~ ^https://([^:@/]+):([^@/]+)@([^/]+)/(.*)$ ]]; then
    user="${BASH_REMATCH[1]}"; token="${BASH_REMATCH[2]}"; host="${BASH_REMATCH[3]}"; path="${BASH_REMATCH[4]}"
    git -C "$repo" remote set-url origin "https://$host/$path"
  else
    [[ "$origin" =~ ^https://([^/]+)/(.*)$ ]]; host="${BASH_REMATCH[1]}"; path="${BASH_REMATCH[2]}"
  fi

  mkdir -p "$(dirname "$cred")"
  ( umask 077; touch "$cred" )
  git -C "$repo" config credential.helper "store --file '$cred'"
  [[ -n "$token" ]] && _jiab_store "$repo" "$cred" "$host" "$user" "$token"

  # 2. does it work?
  if [[ -n "${JIAB_NO_VERIFY:-}" ]] || _jiab_verify "$repo"; then
    echo "   GitHub access OK$( [[ -s "$cred" ]] && echo ' (token saved for updates)' )"
    return 0
  fi

  # 3. ask once
  if ( : </dev/tty ) 2>/dev/null; then   # only prompt if there really is a terminal
    echo "   GitHub needs a token to read this private repo (Settings > Developer settings > Fine-grained tokens,"
    echo "   Contents: Read-only for this repo).  It is stored only on this Pi, in $cred"
    read -rsp "   Paste token (Enter to skip, auto-update will be off): " token </dev/tty; echo
    if [[ -n "$token" ]]; then
      user="${path%%/*}"
      _jiab_store "$repo" "$cred" "$host" "$user" "$token"
      if _jiab_verify "$repo"; then echo "   Token works and is saved."; return 0; fi
      echo "   !! That token didn't work (wrong repo/permissions, or expired). Auto-update will be off until you re-run the installer."
      return 1
    fi
  fi
  echo "   Skipped: auto-update needs GitHub access. Re-run this installer any time to add it."
  return 1
}
