#!/bin/bash

set -eu

fail_setup() {
  if [ -n "${TRIM_TEMP_LOGFILE:-}" ]; then
    printf '%s\n' "$1" > "$TRIM_TEMP_LOGFILE"
  fi
  exit 1
}

require_pkgvar() {
  [ -n "${TRIM_PKGVAR:-}" ] || fail_setup "Nexus data directory is unavailable"
  case "$TRIM_PKGVAR" in
    /*) ;;
    *) fail_setup "Nexus data directory must be absolute" ;;
  esac
}

validate_username() {
  value="$1"
  length=${#value}
  [ "$length" -ge 3 ] && [ "$length" -le 48 ] || fail_setup "Nexus username must contain 3 to 48 characters"
}

validate_password() {
  value="$1"
  [ "${#value}" -ge 8 ] || fail_setup "Nexus password must contain at least 8 characters"
}

validate_hermes_url() {
  case "$1" in
    *\?*|*\#*) fail_setup "Hermes API URL must not include a query or fragment" ;;
  esac
  case "$1" in
    http://*) authority=${1#http://} ;;
    https://*) authority=${1#https://} ;;
    *) fail_setup "Hermes API URL must start with http:// or https://" ;;
  esac
  authority=${authority%%/*}
  authority=${authority%%\?*}
  authority=${authority%%\#*}
  case "$authority" in
    ""|*@*) fail_setup "Hermes API URL must include a host and must not contain credentials" ;;
  esac
}

prepare_setup_dir() {
  mode="$1"
  require_pkgvar
  umask 077
  mkdir -p -- "$TRIM_PKGVAR"
  setup_dir="$TRIM_PKGVAR/.fnos-setup"
  temporary_dir="$TRIM_PKGVAR/.fnos-setup.tmp.$$"
  case "$setup_dir" in
    "$TRIM_PKGVAR"/*) ;;
    *) fail_setup "Unsafe Nexus setup path" ;;
  esac
  rm -rf -- "$temporary_dir"
  mkdir -- "$temporary_dir"
  printf '%s' "$mode" > "$temporary_dir/mode"
}

write_setup_field() {
  field="$1"
  value="$2"
  case "$field" in
    username|password|hermes_api_url|hermes_api_token) ;;
    *) fail_setup "Unknown Nexus setup field" ;;
  esac
  printf '%s' "$value" > "$temporary_dir/$field"
}

commit_setup_dir() {
  rm -rf -- "$setup_dir"
  mv -- "$temporary_dir" "$setup_dir"
  chmod 700 "$setup_dir"
  chmod 600 "$setup_dir"/*
}
