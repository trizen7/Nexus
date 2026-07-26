#!/bin/bash

set -eu

PACKAGED_IMAGE_ARCHIVE="nexus-gateway-image.tar.gz"
PACKAGED_IMAGE_CHECKSUM="nexus-gateway-image.sha256"
PACKAGED_IMAGE_PLATFORM="nexus-gateway-image.platform"
PACKAGED_IMAGE_TAG="nexus-gateway-fnos:0.1.5"

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
    0.0.0.0|0.0.0.0:*|\[::\]|\[::\]:*)
      fail_setup "Hermes API URL cannot use an unspecified address"
      ;;
  esac
}

package_directory_has_image() {
  candidate="$1"
  [ -d "$candidate" ] || return 1
  [ ! -L "$candidate" ] || return 1
  for required in "$PACKAGED_IMAGE_ARCHIVE" "$PACKAGED_IMAGE_CHECKSUM" "$PACKAGED_IMAGE_PLATFORM"; do
    [ -f "$candidate/$required" ] || return 1
    [ ! -L "$candidate/$required" ] || return 1
  done
  return 0
}

find_packaged_docker_dir() {
  root="$1"
  [ -n "$root" ] || return 1
  case "$root" in
    /*) ;;
    *) return 1 ;;
  esac
  for relative in docker app/docker target/docker; do
    candidate="${root%/}/$relative"
    if package_directory_has_image "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

resolve_packaged_docker_dir() {
  if docker_dir=$(find_packaged_docker_dir "${TRIM_PKGINST_TEMP_DIR:-}"); then
    printf '%s\n' "$docker_dir"
    return 0
  fi
  if docker_dir=$(find_packaged_docker_dir "${TRIM_TEMP_UPGRADE_FOLDER:-}"); then
    printf '%s\n' "$docker_dir"
    return 0
  fi
  if docker_dir=$(find_packaged_docker_dir "${TRIM_TEMP_TPKFILE:-}"); then
    printf '%s\n' "$docker_dir"
    return 0
  fi
  fail_setup "The packaged Nexus Gateway image is unavailable"
}

current_docker_platform() {
  machine=$(uname -m 2>/dev/null || true)
  case "$machine" in
    x86_64|amd64) printf '%s\n' "linux/amd64" ;;
    aarch64|arm64) printf '%s\n' "linux/arm64" ;;
    *) fail_setup "This Nexus package does not support device architecture: $machine" ;;
  esac
}

sha256_file() {
  target="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$target" | awk '{print $1}'
    return
  fi
  if command -v busybox >/dev/null 2>&1; then
    busybox sha256sum "$target" | awk '{print $1}'
    return
  fi
  fail_setup "SHA-256 verification is unavailable on this device"
}

load_packaged_image() {
  docker_dir=$(resolve_packaged_docker_dir)
  archive="$docker_dir/$PACKAGED_IMAGE_ARCHIVE"
  checksum_file="$docker_dir/$PACKAGED_IMAGE_CHECKSUM"
  platform_file="$docker_dir/$PACKAGED_IMAGE_PLATFORM"

  packaged_platform=$(cat "$platform_file") || fail_setup "Unable to read the packaged image platform"
  device_platform=$(current_docker_platform)
  [ "$packaged_platform" = "$device_platform" ] || fail_setup "This Nexus package is for $packaged_platform, but this device is $device_platform"

  checksum_line=$(cat "$checksum_file") || fail_setup "Unable to read the packaged image checksum"
  expected_checksum=${checksum_line%%  *}
  checksum_name=${checksum_line#*  }
  [ "$checksum_name" != "$checksum_line" ] || fail_setup "The packaged image checksum file is invalid"
  [ "$checksum_name" = "$PACKAGED_IMAGE_ARCHIVE" ] || fail_setup "The packaged image checksum filename is invalid"
  [ "${#expected_checksum}" -eq 64 ] || fail_setup "The packaged image checksum is invalid"
  invalid_checksum=$(printf '%s' "$expected_checksum" | tr -d '0-9a-f')
  [ -z "$invalid_checksum" ] || fail_setup "The packaged image checksum is invalid"

  actual_checksum=$(sha256_file "$archive")
  [ "$actual_checksum" = "$expected_checksum" ] || fail_setup "The packaged Nexus Gateway image failed SHA-256 verification"

  command -v docker >/dev/null 2>&1 || fail_setup "Docker is required to install Nexus"
  docker load --input "$archive" >/dev/null 2>&1 || fail_setup "The packaged Nexus Gateway image could not be loaded"
  docker image inspect "$PACKAGED_IMAGE_TAG" >/dev/null 2>&1 || fail_setup "The packaged Nexus Gateway image tag is missing"
  loaded_platform=$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$PACKAGED_IMAGE_TAG" 2>/dev/null) || fail_setup "The loaded Nexus Gateway image could not be inspected"
  [ "$loaded_platform" = "$packaged_platform" ] || fail_setup "The loaded Nexus Gateway image architecture is invalid"
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
