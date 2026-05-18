#!/usr/bin/env bash
# Google Drive MCP(@modelcontextprotocol/server-gdrive)용 로컬 인증 헬퍼입니다.
#
# 하는 일:
#   1) ~/.config/gdrive-mcp 폴더 준비
#   2) gcp-oauth.keys.json 이 있으면 ``auth`` 를 돌려 .gdrive-server-credentials.json 저장
#
# 전제:
#   - GCP에서 "데스크톱 앱" OAuth 클라이언트 JSON을 받아
#     ~/.config/gdrive-mcp/gcp-oauth.keys.json 으로 두었을 것

set -euo pipefail

GDRIVE_DIR="${GDRIVE_DIR:-$HOME/.config/gdrive-mcp}"
KEY="$GDRIVE_DIR/gcp-oauth.keys.json"
CRED="$GDRIVE_DIR/.gdrive-server-credentials.json"

mkdir -p "$GDRIVE_DIR"

if [[ ! -f "$KEY" ]]; then
  echo "👉 Google Cloud에서 받은 OAuth 클라이언트(JSON)를 아래 경로에 두고 다시 실행하세요."
  echo "   $KEY"
  exit 1
fi

NODE_BIN="${NODE_BIN:-}"
if [[ -z "$NODE_BIN" ]]; then
  if [[ -x /opt/homebrew/bin/node ]]; then
    NODE_BIN="/opt/homebrew/bin/node"
  elif command -v node &>/dev/null; then
    NODE_BIN="$(command -v node)"
  else
    echo "👉 node 가 없습니다. Homebrew 로 Node를 설치하거나 PATH 에 node 를 넣어 주세요."
    exit 1
  fi
fi

SERVER_JS="${SERVER_JS:-}"
for candidate in \
  "/opt/homebrew/lib/node_modules/@modelcontextprotocol/server-gdrive/dist/index.js" \
  "/usr/local/lib/node_modules/@modelcontextprotocol/server-gdrive/dist/index.js"
do
  if [[ -f "$candidate" ]]; then
    SERVER_JS="$candidate"
    break
  fi
done

if [[ -z "$SERVER_JS" ]]; then
  echo "👉 @modelcontextprotocol/server-gdrive 가 없습니다."
  echo "   macOS 예: npm install -g @modelcontextprotocol/server-gdrive"
  exit 1
fi

export GDRIVE_OAUTH_PATH="$KEY"
export GDRIVE_CREDENTIALS_PATH="$CRED"

echo "👉 브라우저 OAuth가 곧 열립니다. 끝나면 아래 두 파일이 생깁니다."
echo "    $CRED"

"$NODE_BIN" "$SERVER_JS" auth

echo ""
echo "--- Cursor MCP 에 넣을 env 예시(copy 후 경로만 확인) ---"
echo "\"env\": {"
echo "  \"GDRIVE_OAUTH_PATH\": \"$KEY\","
echo "  \"GDRIVE_CREDENTIALS_PATH\": \"$CRED\""
echo "}"
