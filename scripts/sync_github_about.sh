#!/usr/bin/env bash
# Sync the GitHub "About" box (description / homepage / topics).
# Run from a machine where `gh` is logged in as the repo owner.
set -euo pipefail

REPO="${REPO:-HarshShah0203/Scrub}"

DESC='Local offline hygiene for media & documents you own: EXIF/C2PA, hidden Unicode, plus research tools for invisible-watermark robustness. MIT.'
HOME_URL='https://github.com/HarshShah0203/Scrub'
TOPICS='["metadata","privacy","c2pa","exif","python","offline","research","watermark","synthid","unicode","pdf","docx"]'

gh api "repos/${REPO}" -X PATCH \
  -f description="$DESC" \
  -f homepage="$HOME_URL" >/dev/null

gh api -X PUT "repos/${REPO}/topics" \
  -H "Accept: application/vnd.github+json" \
  --input - <<<"{\"names\": ${TOPICS}}" >/dev/null

echo "Updated About for ${REPO}"
gh api "repos/${REPO}" --jq '{description,homepage,topics}'
