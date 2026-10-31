#!/usr/bin/env bash
set -euo pipefail

CLIENT_ID="8a70c774-a3cb-4bdf-8a72-d3fb63611a3f"
TENANT_ID="673ee0ec-b9c2-48e3-86c9-0737bd2e99a9"
RESOURCE_ID="/subscriptions/d760fa4b-978b-4b6d-a62e-93b55bc36746/resourceGroups/theseus-session-search/providers/Microsoft.CognitiveServices/accounts/theseus-translator"
EXPECTED_RESOURCE_NAME="theseus-translator"

CREDENTIAL_DIR="${AZURE_TRANSLATOR_CREDENTIAL_DIR:-/workspace/.credentials/azure-cli-theseus-translator}"
KEY="$CREDENTIAL_DIR/sp-auth-key.pem"
CERT="$CREDENTIAL_DIR/sp-auth-cert.pem"
LOGIN_PEM="$CREDENTIAL_DIR/sp-auth-login.pem"
AZ="${AZURE_TRANSLATOR_AZ:-/workspace/marcopolo-cookbook/azure-cli/az.sh}"
AZURE_CONFIG_DIR="${AZURE_CONFIG_DIR:-/workspace/.config/azure-cli-theseus-translator}"
export AZURE_CONFIG_DIR

fail() {
  printf 'AZURE_TRANSLATOR_SP_LOGIN_BLOCKED reason=%s\n' "$1" >&2
  exit 1
}

[[ -x "$AZ" ]] || fail "azure_cli_missing"
[[ -f "$KEY" && ! -L "$KEY" ]] || fail "private key missing"
[[ -f "$CERT" && ! -L "$CERT" ]] || fail "certificate missing"

UID_ACTUAL="$(id -u)"
for file in "$KEY" "$CERT"; do
  [[ "$(stat -c '%u' "$file")" == "$UID_ACTUAL" ]] || fail "credential_owner_mismatch"
  [[ "$(stat -c '%a' "$file")" == "600" ]] || fail "credential_mode_mismatch"
done

umask 077
mkdir -p "$CREDENTIAL_DIR" "$AZURE_CONFIG_DIR"
chmod 700 "$CREDENTIAL_DIR" "$AZURE_CONFIG_DIR"

TMP_LOGIN="$CREDENTIAL_DIR/.sp-auth-login.pem.tmp.$$"
trap 'rm -f "$TMP_LOGIN"' EXIT
cat "$KEY" "$CERT" >"$TMP_LOGIN"
chmod 600 "$TMP_LOGIN"
mv "$TMP_LOGIN" "$LOGIN_PEM"
trap - EXIT

"$AZ" login \
  --service-principal \
  --username "$CLIENT_ID" \
  --certificate "$LOGIN_PEM" \
  --tenant "$TENANT_ID" \
  --allow-no-subscriptions \
  --output none

RESOURCE_NAME="$(
  "$AZ" resource show \
    --ids "$RESOURCE_ID" \
    --query name \
    --output tsv
)"

[[ "$RESOURCE_NAME" == "$EXPECTED_RESOURCE_NAME" ]] || fail "translator_resource_readback_mismatch"

printf 'AZURE_TRANSLATOR_SP_LOGIN_VERIFIED client_id=%s tenant_id=%s resource=%s\n' \
  "$CLIENT_ID" "$TENANT_ID" "$RESOURCE_NAME"
