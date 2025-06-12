#!/bin/bash

set -eu

script_dir=$(cd "$(dirname "$0")" && pwd)

uri_base="https://github.com/quel-inc/quelware/releases/download"
version="0.8.13"
archive_name="quelware_prebuilt.tgz"
extract_dir="${script_dir}/.prebuilt"

tmp_dir=$(mktemp -d)
trap 'rm -rf -- "$tmp_dir"' EXIT

download_path="${tmp_dir}/${archive_name}"

echo "INFO: Downloading ${uri_base}/${version}/${archive_name} ..."
wget -q "${uri_base}/${version}/${archive_name}" -O "${download_path}" || {
    echo "ERROR: No prebuilt archive is available for ${version}" >&2
    exit 1
}
echo "INFO: Download completed."

echo "INFO: Preparing extraction directory '${extract_dir}' ..."
rm -rf "${extract_dir}"
mkdir -p "${extract_dir}"

echo "INFO: Extracting ${archive_name} to '${extract_dir}'..."
tar -xzf "${download_path}" -C "${extract_dir}"

echo "INFO: Extraction completed successfully into '${extract_dir}' directory."

