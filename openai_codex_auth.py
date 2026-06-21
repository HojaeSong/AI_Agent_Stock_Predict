"""
OpenAI Codex OAuth 인증 + API 호출 라이브러리

Codex CLI와 동일한 방식으로 ChatGPT OAuth 인증 후
chatgpt.com/backend-api/codex 엔드포인트를 통해 API를 호출합니다.

사용법:
    from openai_codex_auth import authenticate, get_client, chat

    # 1. 인증 (처음 한번만, 이후 자동 리프레시)
    authenticate()

    # 2. 간단한 채팅
    answer = chat("안녕하세요!")
    print(answer)

    # 3. openai 클라이언트 직접 사용
    client = get_client()
    resp = client.responses.create(model="gpt-5.4", input="질문")
"""

import base64
import hashlib
import json
import os
import secrets
import sys
import threading
import time
import webbrowser
import urllib.request
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs
from typing import Optional

import openai

# ─── OpenAI Codex OAuth 상수 ──────────────────────────────────────────────────
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"
REDIRECT_URI = "http://localhost:1455/auth/callback"
SCOPE = "openid profile email offline_access"
CALLBACK_PORT = 1455

# ⭐ ChatGPT OAuth 토큰은 이 URL을 사용 (Codex CLI 소스코드에서 확인)
#    일반 API 키는 https://api.openai.com/v1 을 사용하지만
#    ChatGPT 계정(OAuth) 인증은 별도 백엔드를 사용합니다.
CHATGPT_API_BASE_URL = "https://chatgpt.com/backend-api/codex"

# 기본 모델
DEFAULT_MODEL = "gpt-5.4"

# ─── 저장 경로 ────────────────────────────────────────────────────────────────
_SCRIPT_DIR = Path(__file__).parent
_AUTH_FILE = _SCRIPT_DIR / "codex_auth.json"

# ─── 모듈 레벨 캐시 ──────────────────────────────────────────────────────────
_cached_auth: Optional[dict] = None
_cached_client: Optional[openai.OpenAI] = None


# ═══════════════════════════════════════════════════════════════════════════════
# 내부 유틸
# ═══════════════════════════════════════════════════════════════════════════════

def _generate_pkce() -> tuple:
    verifier_bytes = secrets.token_bytes(32)
    verifier = base64.urlsafe_b64encode(verifier_bytes).rstrip(b"=").decode("ascii")
    challenge_hash = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(challenge_hash).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _decode_jwt_payload(token: str) -> Optional[dict]:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload = parts[1]
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += "=" * padding
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return None


def _get_account_id(access_token: str) -> Optional[str]:
    payload = _decode_jwt_payload(access_token)
    if not payload:
        return None
    auth_claim = payload.get("https://api.openai.com/auth", {})
    account_id = auth_claim.get("chatgpt_account_id")
    return account_id if isinstance(account_id, str) and account_id else None


def _is_remote_environment() -> bool:
    if os.environ.get("SSH_CLIENT") or os.environ.get("SSH_TTY") or os.environ.get("SSH_CONNECTION"):
        return True
    if os.environ.get("REMOTE_CONTAINERS") or os.environ.get("CODESPACES"):
        return True
    if sys.platform == "linux" and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        return True
    return False


def _parse_manual_input(raw: str) -> tuple:
    value = raw.strip()
    if not value:
        return None, None
    try:
        parsed = urlparse(value)
        params = parse_qs(parsed.query)
        code = params.get("code", [None])[0]
        state = params.get("state", [None])[0]
        if code:
            return code, state
    except Exception:
        pass
    if "code=" in value:
        params = parse_qs(value)
        return params.get("code", [None])[0], params.get("state", [None])[0]
    return value, None


# ═══════════════════════════════════════════════════════════════════════════════
# 콜백 서버
# ═══════════════════════════════════════════════════════════════════════════════

class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    authorization_code: Optional[str] = None
    received_state: Optional[str] = None

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != "/auth/callback":
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")
            return

        params = parse_qs(parsed.query)
        code = params.get("code", [None])[0]
        state = params.get("state", [None])[0]

        if not code:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Missing authorization code")
            return

        _OAuthCallbackHandler.authorization_code = code
        _OAuthCallbackHandler.received_state = state

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        html = '<!doctype html><html><head><meta charset="utf-8"><title>인증 성공</title></head>'
        html += '<body><h2>✅ 인증 성공!</h2><p>이 창을 닫고 터미널로 돌아가세요.</p></body></html>'
        self.wfile.write(html.encode("utf-8"))

    def log_message(self, fmt, *args):
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# 토큰 교환 / 리프레시
# ═══════════════════════════════════════════════════════════════════════════════

def _exchange_code_for_tokens(code: str, verifier: str) -> dict:
    data = urlencode({
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": REDIRECT_URI,
    }).encode("utf-8")

    req = urllib.request.Request(
        TOKEN_URL, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        body = json.loads(resp.read().decode("utf-8"))

    if "access_token" not in body:
        raise RuntimeError(f"토큰 교환 실패: {body}")

    return {
        "access_token": body["access_token"],
        "refresh_token": body.get("refresh_token"),
        "expires_in": body.get("expires_in"),
        "expires_at": int(time.time()) + body.get("expires_in", 0),
        "token_type": body.get("token_type", "Bearer"),
    }


def _refresh_tokens(refresh_token: str) -> dict:
    data = urlencode({
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "refresh_token": refresh_token,
    }).encode("utf-8")

    req = urllib.request.Request(
        TOKEN_URL, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        body = json.loads(resp.read().decode("utf-8"))

    if "access_token" not in body:
        raise RuntimeError(f"토큰 리프레시 실패: {body}")

    return {
        "access_token": body["access_token"],
        "refresh_token": body.get("refresh_token", refresh_token),
        "expires_in": body.get("expires_in"),
        "expires_at": int(time.time()) + body.get("expires_in", 0),
        "token_type": body.get("token_type", "Bearer"),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 저장 / 로드
# ═══════════════════════════════════════════════════════════════════════════════

def _save_auth(data: dict, auth_file: Path = _AUTH_FILE):
    auth_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(auth_file, 0o600)
    except OSError:
        pass


def _load_auth(auth_file: Path = _AUTH_FILE) -> Optional[dict]:
    if not auth_file.exists():
        return None
    try:
        return json.loads(auth_file.read_text(encoding="utf-8"))
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# 공개 API: 인증
# ═══════════════════════════════════════════════════════════════════════════════

def authenticate(force_login: bool = False, auth_file: Optional[str] = None) -> dict:
    """
    OpenAI Codex OAuth 인증을 수행합니다.
    이미 인증 정보가 있으면 자동으로 리프레시합니다.

    Returns:
        dict: access_token, refresh_token, account_id 등
    """
    global _cached_auth, _cached_client

    target_file = Path(auth_file) if auth_file else _AUTH_FILE

    if not force_login:
        existing = _cached_auth or _load_auth(target_file)
        if existing and existing.get("refresh_token"):
            expires_at = existing.get("expires_at", 0)
            if time.time() < expires_at - 300:
                _cached_auth = existing
                _cached_client = None
                return existing

            try:
                print("🔄 토큰 리프레시 중...")
                refreshed = _refresh_tokens(existing["refresh_token"])
                account_id = _get_account_id(refreshed["access_token"])
                auth_data = {**refreshed, "account_id": account_id, "provider": "openai-codex"}
                _save_auth(auth_data, target_file)
                _cached_auth = auth_data
                _cached_client = None
                print("✅ 토큰 리프레시 성공!")
                return auth_data
            except Exception as e:
                print(f"⚠️  리프레시 실패: {e}")
                print("   새로 로그인합니다...")

    # 새 로그인 플로우
    print("=" * 50)
    print("  OpenAI Codex OAuth 로그인")
    print("=" * 50)

    verifier, challenge = _generate_pkce()
    state = secrets.token_hex(16)

    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "originator": "python-cli",
    }
    auth_url = f"{AUTHORIZE_URL}?{urlencode(params)}"

    is_remote = _is_remote_environment()

    if is_remote:
        print("\n🌐 원격 환경 감지됨. 아래 URL을 로컬 브라우저에서 열어주세요:\n")
        print(f"   {auth_url}\n")
        print("   로그인 후 리다이렉트된 URL을 붙여넣으세요.\n")

        while True:
            raw = input("🔗 리다이렉트 URL: ").strip()
            code, recv_state = _parse_manual_input(raw)
            if not code:
                print("❌ 인증 코드를 파싱할 수 없습니다.")
                continue
            if recv_state and recv_state != state:
                raise RuntimeError("state 불일치!")
            break
    else:
        _OAuthCallbackHandler.authorization_code = None
        _OAuthCallbackHandler.received_state = None

        server = HTTPServer(("127.0.0.1", CALLBACK_PORT), _OAuthCallbackHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        print("\n🌐 브라우저에서 로그인 페이지를 엽니다...")
        webbrowser.open(auth_url)
        print("⏳ 브라우저에서 로그인을 완료하세요... (최대 120초 대기)\n")

        for _ in range(1200):
            if _OAuthCallbackHandler.authorization_code:
                break
            time.sleep(0.1)

        server.shutdown()

        code = _OAuthCallbackHandler.authorization_code
        recv_state = _OAuthCallbackHandler.received_state

        if not code:
            print("⏱️  타임아웃! 수동으로 입력하세요.")
            raw = input("🔗 리다이렉트 URL: ").strip()
            code, recv_state = _parse_manual_input(raw)

        if not code:
            raise RuntimeError("인증 코드를 받지 못했습니다.")
        if recv_state and recv_state != state:
            raise RuntimeError("state 불일치!")

    print("🔑 토큰 교환 중...")
    tokens = _exchange_code_for_tokens(code, verifier)
    account_id = _get_account_id(tokens["access_token"])

    auth_data = {**tokens, "account_id": account_id, "provider": "openai-codex"}
    _save_auth(auth_data, target_file)
    _cached_auth = auth_data
    _cached_client = None

    print(f"🎉 인증 완료! (account_id: {account_id})")
    print(f"   저장 위치: {target_file}")
    return auth_data


def get_access_token(force_refresh: bool = False) -> str:
    """현재 유효한 access_token을 반환합니다."""
    global _cached_auth, _cached_client

    auth = _cached_auth or _load_auth()
    if not auth or not auth.get("access_token"):
        raise RuntimeError("인증 정보가 없습니다. 먼저 authenticate()를 호출하세요.")

    expires_at = auth.get("expires_at", 0)

    if force_refresh or time.time() >= expires_at - 300:
        if not auth.get("refresh_token"):
            raise RuntimeError("refresh_token이 없습니다. 다시 authenticate()를 호출하세요.")
        refreshed = _refresh_tokens(auth["refresh_token"])
        account_id = _get_account_id(refreshed["access_token"])
        auth_data = {**refreshed, "account_id": account_id, "provider": "openai-codex"}
        _save_auth(auth_data)
        _cached_auth = auth_data
        _cached_client = None
        return auth_data["access_token"]

    _cached_auth = auth
    return auth["access_token"]


# ═══════════════════════════════════════════════════════════════════════════════
# 공개 API: OpenAI 클라이언트 & 모델 사용
# ═══════════════════════════════════════════════════════════════════════════════

def get_client() -> openai.OpenAI:
    """
    OAuth 토큰이 설정된 OpenAI 클라이언트를 반환합니다.

    ⭐ ChatGPT OAuth 인증은 api.openai.com이 아니라
       chatgpt.com/backend-api/codex 를 base URL로 사용합니다.
       (Codex CLI 소스코드에서 확인됨)

    Returns:
        openai.OpenAI: 인증된 클라이언트

    사용 예시:
        client = get_client()
        resp = client.responses.create(
            model="gpt-5.4",
            input="안녕하세요!",
        )
        print(resp.output_text)
    """
    global _cached_client

    token = get_access_token()

    if _cached_client is None:
        _cached_client = openai.OpenAI(
            api_key=token,
            base_url=CHATGPT_API_BASE_URL,
        )

    return _cached_client


def chat(
    message: str,
    model: str = DEFAULT_MODEL,
    system_prompt: Optional[str] = None,
    input_messages: Optional[list] = None,
    max_output_tokens: Optional[int] = None,
    stream: bool = False,
    previous_response_id: Optional[str] = None,
):
    """
    OpenAI Responses API를 통해 채팅합니다.

    Args:
        message: 사용자 메시지
        model: 사용할 모델 (기본값: gpt-5.4)
        system_prompt: 시스템 프롬프트 (instructions)
        input_messages: 대화 이력 리스트
        max_output_tokens: 최대 응답 토큰 수
        stream: True면 스트리밍 Generator 반환
        previous_response_id: 이전 응답 ID (대화 연속성)

    Returns:
        str: 응답 텍스트 (stream=False)
        Generator[str]: 스트리밍 텍스트 청크 (stream=True)

    사용 예시:
        answer = chat("파이썬이 뭐야?")
        answer = chat("번역해줘: Hello", system_prompt="너는 번역가야")

        for chunk in chat("긴 답변 부탁", stream=True):
            print(chunk, end="", flush=True)
    """
    client = get_client()

    # ChatGPT 백엔드는 input이 반드시 메시지 리스트 형식이어야 함
    if input_messages is not None:
        input_data = input_messages
    else:
        input_data = [{"role": "user", "content": message}]

    kwargs = {
        "model": model,
        "input": input_data,
        "stream": True,  # ChatGPT 백엔드는 항상 스트리밍 필수
        "store": False,  # ChatGPT 백엔드 필수
    }

    # ChatGPT 백엔드는 instructions 필드 필수
    kwargs["instructions"] = system_prompt or ""
    if max_output_tokens is not None:
        kwargs["max_output_tokens"] = max_output_tokens
    if previous_response_id:
        kwargs["previous_response_id"] = previous_response_id

    if stream:
        # 스트리밍: Generator로 청크 반환
        return _stream_response(client, kwargs)
    else:
        # 비스트리밍: 내부적으로 스트리밍 받아서 전체 텍스트 반환
        chunks = []
        for chunk in _stream_response(client, kwargs):
            chunks.append(chunk)
        return "".join(chunks)


def _stream_response(client: openai.OpenAI, kwargs: dict):
    """스트리밍 응답을 Generator로 반환"""
    stream = client.responses.create(**kwargs)
    for event in stream:
        if event.type == "response.output_text.delta":
            yield event.delta


def chat_with_history(
    conversation: list,
    message: str,
    model: str = DEFAULT_MODEL,
    system_prompt: Optional[str] = None,
    **kwargs,
) -> str:
    """
    대화 이력을 유지하면서 대화합니다.

    사용 예시:
        history = []
        r1 = chat_with_history(history, "내 이름은 호재야", system_prompt="친절하게 대화해")
        r2 = chat_with_history(history, "내 이름이 뭐야?")
    """
    conversation.append({"role": "user", "content": message})

    response = chat(
        "",
        model=model,
        system_prompt=system_prompt,
        input_messages=conversation,
        stream=False,
        **kwargs,
    )

    conversation.append({"role": "assistant", "content": response})
    return response


def chat_full(
    message: str,
    model: str = DEFAULT_MODEL,
    system_prompt: Optional[str] = None,
    input_messages: Optional[list] = None,
    max_output_tokens: Optional[int] = None,
    previous_response_id: Optional[str] = None,
) -> dict:
    """
    chat()과 동일하지만 전체 응답 정보를 반환합니다.

    사용 예시:
        r1 = chat_full("내 이름은 호재야")
        r2 = chat_full("내 이름 뭐야?", previous_response_id=r1["response_id"])
    """
    client = get_client()
    if input_messages is not None:
        input_data = input_messages
    else:
        input_data = [{"role": "user", "content": message}]

    kwargs = {
        "model": model,
        "input": input_data,
        "store": False,
        "stream": True,
    }

    kwargs["instructions"] = system_prompt or ""
    if max_output_tokens is not None:
        kwargs["max_output_tokens"] = max_output_tokens
    if previous_response_id:
        kwargs["previous_response_id"] = previous_response_id

    # 스트리밍으로 받아서 전체 정보 수집
    stream = client.responses.create(**kwargs)
    chunks = []
    response_id = None
    result_model = None
    usage = None

    for event in stream:
        if event.type == "response.output_text.delta":
            chunks.append(event.delta)
        elif event.type == "response.completed":
            resp = event.response
            response_id = resp.id
            result_model = resp.model
            usage = resp.usage

    return {
        "text": "".join(chunks),
        "response_id": response_id,
        "model": result_model,
        "usage": usage,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# CLI로 직접 실행 시 테스트
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    auth = authenticate()
    print(f"\n✅ access_token (앞 50자): {auth['access_token'][:50]}...")
    print(f"   account_id: {auth.get('account_id')}")
    print(f"   base_url: {CHATGPT_API_BASE_URL}")

    try:
        print("\n📝 테스트 메시지 전송 중...")
        reply = chat("안녕하세요! 한 줄로 자기소개 해주세요.", model=DEFAULT_MODEL)
        print(f"\n🤖 응답:\n{reply}")
    except Exception as e:
        print(f"\n⚠️  모델 호출 실패: {e}")
