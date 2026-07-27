#!/bin/bash

set -eu

RUNTIME_PLATFORM_FILE="runtime.platform"
RUNTIME_CHECKSUM_FILE="runtime.sha256"
RUNTIME_EXECUTABLE="nexus-gateway/nexus-gateway"
RUNTIME_CA_BUNDLE="ca-certificates.crt"

fail_setup() {
  local message="$1"
  if [ -n "${TRIM_TEMP_LOGFILE:-}" ]; then
    printf '%s\n' "$message" > "$TRIM_TEMP_LOGFILE"
  fi
  exit 1
}

require_absolute_directory() {
  local value="$1"
  local label="$2"
  [ -n "$value" ] || fail_setup "$label is unavailable"
  case "$value" in
    /*) ;;
    *) fail_setup "$label must be absolute" ;;
  esac
}

require_pkgvar() {
  require_absolute_directory "${TRIM_PKGVAR:-}" "Nexus data directory"
  umask 077
  mkdir -p -- "$TRIM_PKGVAR" || fail_setup "Nexus data directory could not be created"
  [ -d "$TRIM_PKGVAR" ] && [ ! -L "$TRIM_PKGVAR" ] || fail_setup "Nexus data directory is unsafe"
}

validate_username() {
  local value="$1"
  local length=${#value}
  [ "$length" -ge 3 ] && [ "$length" -le 48 ] || fail_setup "Nexus username must contain 3 to 48 characters"
}

validate_password() {
  local value="$1"
  [ "${#value}" -ge 8 ] || fail_setup "Nexus password must contain at least 8 characters"
}

validate_hermes_url() {
  local value="$1"
  local authority
  case "$value" in
    *\?*|*\#*) fail_setup "Hermes API URL must not include a query or fragment" ;;
  esac
  case "$value" in
    http://*) authority=${value#http://} ;;
    https://*) authority=${value#https://} ;;
    *) fail_setup "Hermes API URL must start with http:// or https://" ;;
  esac
  authority=${authority%%/*}
  authority=${authority%%\?*}
  authority=${authority%%\#*}
  case "$authority" in
    ""|*@*) fail_setup "Hermes API URL must include a host and must not contain credentials" ;;
    0.0.0.0|0.0.0.0:*|\[::\]|\[::\]:*)
      fail_setup "Hermes API URL cannot use an unspecified address"
      ;;
  esac
}

current_runtime_platform() {
  local machine
  machine=$(uname -m 2>/dev/null || true)
  case "$machine" in
    x86_64|amd64) printf '%s\n' "linux/amd64" ;;
    aarch64|arm64) printf '%s\n' "linux/arm64" ;;
    *) fail_setup "This Nexus package does not support device architecture: $machine" ;;
  esac
}

sha256_file() {
  local target="$1"
  local output
  if command -v sha256sum >/dev/null 2>&1; then
    output=$(sha256sum "$target") || fail_setup "SHA-256 verification failed"
    printf '%s\n' "${output%% *}"
    return
  fi
  if command -v busybox >/dev/null 2>&1; then
    output=$(busybox sha256sum "$target") || fail_setup "SHA-256 verification failed"
    printf '%s\n' "${output%% *}"
    return
  fi
  fail_setup "SHA-256 verification is unavailable on this device"
}

validate_runtime_elf() {
  local executable="$1"
  local platform="$2"
  local header
  local machine_hex
  command -v od >/dev/null 2>&1 || fail_setup "ELF verification is unavailable on this device"
  command -v tr >/dev/null 2>&1 || fail_setup "ELF verification is unavailable on this device"
  command -v cut >/dev/null 2>&1 || fail_setup "ELF verification is unavailable on this device"
  header=$(od -An -tx1 -N20 "$executable" 2>/dev/null | tr -d ' \r\n') || fail_setup "Nexus Gateway executable could not be inspected"
  case "$header" in
    7f454c460201*) ;;
    *) fail_setup "Nexus Gateway executable is not a 64-bit little-endian ELF binary" ;;
  esac
  machine_hex=$(printf '%s' "$header" | cut -c 37-40)
  case "$platform:$machine_hex" in
    linux/amd64:3e00|linux/arm64:b700) ;;
    *) fail_setup "Nexus Gateway executable architecture does not match this package" ;;
  esac
}

runtime_directory_is_complete() {
  local candidate="$1"
  local required
  [ -d "$candidate" ] || return 1
  [ ! -L "$candidate" ] || return 1
  for required in "$RUNTIME_PLATFORM_FILE" "$RUNTIME_CHECKSUM_FILE" "$RUNTIME_EXECUTABLE" "$RUNTIME_CA_BUNDLE"; do
    [ -f "$candidate/$required" ] || return 1
    [ ! -L "$candidate/$required" ] || return 1
  done
  [ -x "$candidate/$RUNTIME_EXECUTABLE" ] || return 1
  return 0
}

find_packaged_runtime_dir() {
  local root="$1"
  local relative
  local candidate
  [ -n "$root" ] || return 1
  case "$root" in
    /*) ;;
    *) return 1 ;;
  esac
  [ -d "$root" ] || return 1
  for relative in runtime app/runtime target/runtime; do
    candidate="${root%/}/$relative"
    if runtime_directory_is_complete "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

resolve_packaged_runtime_dir() {
  local runtime_dir
  if runtime_dir=$(find_packaged_runtime_dir "${TRIM_PKGINST_TEMP_DIR:-}"); then
    printf '%s\n' "$runtime_dir"
    return 0
  fi
  if runtime_dir=$(find_packaged_runtime_dir "${TRIM_TEMP_UPGRADE_FOLDER:-}"); then
    printf '%s\n' "$runtime_dir"
    return 0
  fi
  if runtime_dir=$(find_packaged_runtime_dir "${TRIM_TEMP_TPKFILE:-}"); then
    printf '%s\n' "$runtime_dir"
    return 0
  fi
  fail_setup "The packaged Nexus Gateway runtime is unavailable"
}

validate_runtime_dir() {
  local runtime_dir="$1"
  local packaged_platform
  local device_platform
  local checksum_manifest
  local line
  local expected_checksum
  local relative
  local target
  local actual_checksum
  local listed_paths=""
  local listed_count=0
  local actual_count=0

  runtime_directory_is_complete "$runtime_dir" || fail_setup "The packaged Nexus Gateway runtime is incomplete"
  command -v find >/dev/null 2>&1 || fail_setup "Runtime file verification is unavailable on this device"

  if [ -n "$(find "$runtime_dir" -type l -print -quit 2>/dev/null)" ]; then
    fail_setup "The packaged Nexus Gateway runtime contains a symbolic link"
  fi

  packaged_platform=$(cat "$runtime_dir/$RUNTIME_PLATFORM_FILE") || fail_setup "Unable to read the packaged runtime platform"
  device_platform=$(current_runtime_platform)
  [ "$packaged_platform" = "$device_platform" ] || fail_setup "This Nexus package is for $packaged_platform, but this device is $device_platform"
  validate_runtime_elf "$runtime_dir/$RUNTIME_EXECUTABLE" "$packaged_platform"

  checksum_manifest="$runtime_dir/$RUNTIME_CHECKSUM_FILE"
  while IFS= read -r -d '' target; do
    [ "$target" = "$checksum_manifest" ] || actual_count=$((actual_count + 1))
  done < <(find "$runtime_dir" -type f -print0)

  while IFS= read -r line || [ -n "$line" ]; do
    [ -n "$line" ] || fail_setup "The packaged runtime checksum manifest contains an empty line"
    expected_checksum=${line%%  *}
    relative=${line#*  }
    [ "$relative" != "$line" ] || fail_setup "The packaged runtime checksum manifest is invalid"
    [ "${#expected_checksum}" -eq 64 ] || fail_setup "The packaged runtime checksum is invalid"
    case "$expected_checksum" in
      *[!0-9a-f]*) fail_setup "The packaged runtime checksum is invalid" ;;
    esac
    case "$relative" in
      ""|/*|.|..|../*|*/../*|*/..|*\\*|"$RUNTIME_CHECKSUM_FILE")
        fail_setup "The packaged runtime checksum path is unsafe"
        ;;
    esac
    case $'\n'"$listed_paths"$'\n' in
      *$'\n'"$relative"$'\n'*) fail_setup "The packaged runtime checksum manifest contains a duplicate file" ;;
    esac
    if [ -n "$listed_paths" ]; then
      listed_paths="$listed_paths"$'\n'"$relative"
    else
      listed_paths="$relative"
    fi
    listed_count=$((listed_count + 1))

    target="$runtime_dir/$relative"
    [ -f "$target" ] && [ ! -L "$target" ] || fail_setup "The packaged runtime checksum references a missing file"
    actual_checksum=$(sha256_file "$target")
    [ "$actual_checksum" = "$expected_checksum" ] || fail_setup "The packaged Nexus Gateway runtime failed SHA-256 verification"
  done < "$checksum_manifest"

  [ "$listed_count" -eq "$actual_count" ] || fail_setup "The packaged runtime checksum file set is incomplete"
}
validate_packaged_runtime() {
  local runtime_dir
  runtime_dir=$(resolve_packaged_runtime_dir)
  validate_runtime_dir "$runtime_dir"
}

prepare_setup_dir() {
  local mode="$1"
  require_pkgvar
  umask 077
  setup_dir="$TRIM_PKGVAR/.fnos-setup"
  temporary_dir="$TRIM_PKGVAR/.fnos-setup.tmp.$$"
  case "$setup_dir:$temporary_dir" in
    "$TRIM_PKGVAR"/.fnos-setup:"$TRIM_PKGVAR"/.fnos-setup.tmp.*) ;;
    *) fail_setup "Unsafe Nexus setup path" ;;
  esac
  if [ -e "$setup_dir" ] || [ -L "$setup_dir" ]; then
    [ -d "$setup_dir" ] && [ ! -L "$setup_dir" ] || fail_setup "Unsafe Nexus setup directory"
  fi
  rm -rf -- "$temporary_dir"
  mkdir -- "$temporary_dir" || fail_setup "Nexus setup directory could not be created"
  printf '%s' "$mode" > "$temporary_dir/mode"
}

write_setup_field() {
  local field="$1"
  local value="$2"
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