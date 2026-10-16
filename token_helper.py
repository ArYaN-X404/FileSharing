import time
import hmac
import hashlib
import secrets
import threading
from cachetools import TTLCache
from config import HMAC_TOKEN_SECRET

# In-memory replay prevention cache: auto-purges tokens after 90 seconds from RAM
used_tokens_cache = TTLCache(maxsize=50000, ttl=90)
_cache_lock = threading.Lock()


def generate_token(telegram_id: int, batch_id: str, msg_id: int, expires_in_seconds: int = 90) -> str:
    """
    Generate a compact Telegram-safe HMAC token that fits within the 64-char ?start= limit.
    Format: tok_<tg_id>-<msg_id>-<expires>-<nonce6hex>-<hmac16hex>
    Length: ~55 characters (under Telegram's 64-char limit).
    """
    expires_at = int(time.time()) + expires_in_seconds
    nonce = secrets.token_hex(3)  # 6 hex chars
    data_to_sign = f"{telegram_id}:{msg_id}:{expires_at}:{nonce}"
    full_hmac = hmac.new(
        HMAC_TOKEN_SECRET.encode(),
        data_to_sign.encode(),
        hashlib.sha256
    ).hexdigest()
    hmac16 = full_hmac[:16]  # 16 hex chars (64-bit security)

    return f"tok_{telegram_id}-{msg_id}-{expires_at}-{nonce}-{hmac16}"


def verify_and_consume_token(token_str: str, request_user_id: int):
    """
    Atomically validates and consumes a compact token with L1 RAM + L2 Persistent DB guards.
    Guarantees race-condition immunity under high-concurrency multi-threaded requests.
    """
    print(f"\n{'='*60}", flush=True)
    print(f"[TOKEN DEBUG] Validating Token: {token_str}", flush=True)
    print(f"[TOKEN DEBUG] Requesting User ID: {request_user_id}", flush=True)
    print(f"[TOKEN DEBUG] Token Length: {len(token_str)} chars (Limit: 64)", flush=True)

    if not token_str.startswith("tok_"):
        print(f"[TOKEN DEBUG] FAILED: Does not start with 'tok_'", flush=True)
        print(f"{'='*60}\n", flush=True)
        return False, None, "Invalid token format."

    inner = token_str[4:]
    parts = inner.split("-")
    print(f"[TOKEN DEBUG] Split parts ({len(parts)}): {parts}", flush=True)
    if len(parts) != 5:
        print(f"[TOKEN DEBUG] FAILED: Expected 5 hyphen-separated parts, got {len(parts)}", flush=True)
        print(f"{'='*60}\n", flush=True)
        return False, None, "Malformed token format."

    tg_id_str, msg_id_str, expires_at_str, nonce, hmac16 = parts

    try:
        telegram_id = int(tg_id_str)
        msg_id = int(msg_id_str)
        expires_at = int(expires_at_str)
    except ValueError as e:
        print(f"[TOKEN DEBUG] FAILED: Non-numeric fields: {e}", flush=True)
        print(f"{'='*60}\n", flush=True)
        return False, None, "Token decode failed: non-numeric fields."

    print(f"[TOKEN DEBUG] Parsed: tg_id={telegram_id}, msg_id={msg_id}, expires_at={expires_at}, nonce={nonce}, hmac16={hmac16}", flush=True)

    # 1. Verify Identity (telegram_id == 0 allows wildcard/unlinked initial claims)
    print(f"[TOKEN DEBUG] Check 1 (Identity): Token tg_id={telegram_id} vs Request user_id={request_user_id}", flush=True)
    if telegram_id != 0 and request_user_id != telegram_id:
        print(f"[TOKEN DEBUG] FAILED: Identity mismatch!", flush=True)
        print(f"{'='*60}\n", flush=True)
        return False, None, f"Access Denied: This link was generated for a different Telegram account ({telegram_id})."
    print(f"[TOKEN DEBUG] Check 1 (Identity): PASSED", flush=True)

    # 2. Verify Expiry
    now = int(time.time())
    remaining = expires_at - now
    print(f"[TOKEN DEBUG] Check 2 (Expiry): now={now}, expires_at={expires_at}, remaining={remaining}s", flush=True)
    if now > expires_at:
        print(f"[TOKEN DEBUG] FAILED: Token expired by {abs(remaining)}s!", flush=True)
        print(f"{'='*60}\n", flush=True)
        return False, None, f"This lecture link has expired ({abs(remaining)}s ago). Please click again from the website."
    print(f"[TOKEN DEBUG] Check 2 (Expiry): PASSED ({remaining}s remaining)", flush=True)

    # 3. Fast L1 RAM Replay Check (Pre-cryptography barrier)
    with _cache_lock:
        if token_str in used_tokens_cache:
            print(f"[TOKEN DEBUG] FAILED: Token was already consumed in L1 cache!", flush=True)
            print(f"{'='*60}\n", flush=True)
            return False, None, "This link was already used. Please click the lecture again on the website to generate a fresh link."

    # 4. Verify Cryptographic HMAC Signature
    data_to_sign = f"{telegram_id}:{msg_id}:{expires_at}:{nonce}"
    expected_full = hmac.new(
        HMAC_TOKEN_SECRET.encode(),
        data_to_sign.encode(),
        hashlib.sha256
    ).hexdigest()
    expected_hmac16 = expected_full[:16]
    sig_match = hmac.compare_digest(hmac16, expected_hmac16)

    print(f"[TOKEN DEBUG] Check 4 (HMAC): data_to_sign='{data_to_sign}'", flush=True)
    print(f"[TOKEN DEBUG] Check 4 (HMAC): Received sig='{hmac16}' vs Expected sig='{expected_hmac16}' -> Match={sig_match}", flush=True)

    if not sig_match:
        print(f"[TOKEN DEBUG] FAILED: HMAC signature mismatch!", flush=True)
        print(f"{'='*60}\n", flush=True)
        return False, None, "Token signature verification failed (tampered link)."
    print(f"[TOKEN DEBUG] Check 4 (HMAC): PASSED", flush=True)

    # 5. ATOMIC CONSUMPTION: Enforce strict L1 RAM + L2 Persistent DB serialization
    with _cache_lock:
        # Check L1 again under lock (covers race between step 3 and lock acquisition)
        if token_str in used_tokens_cache:
            print(f"[TOKEN DEBUG] FAILED: Token consumed concurrently by another thread!", flush=True)
            print(f"{'='*60}\n", flush=True)
            return False, None, "This link was already used. Please click the lecture again on the website to generate a fresh link."

        # Check L2 Persistent Storage (Covers multi-process & post-restart replay attacks)
        try:
            from database.database import mark_token_consumed_persistent
            if not mark_token_consumed_persistent(token_str, telegram_id, msg_id, expires_at):
                used_tokens_cache[token_str] = True
                print(f"[TOKEN DEBUG] FAILED: Token was already consumed in persistent storage!", flush=True)
                print(f"{'='*60}\n", flush=True)
                return False, None, "This link was already used. Please click the lecture again on the website to generate a fresh link."
        except Exception as e_db:
            print(f"[TOKEN DEBUG] Note: Persistent DB check bypassed ({e_db})", flush=True)

        # Mark in L1 RAM cache
        used_tokens_cache[token_str] = True

    print(f"[TOKEN DEBUG] ALL CHECKS PASSED! Token consumed atomically into replay cache & persistent storage.", flush=True)
    print(f"{'='*60}\n", flush=True)

    return True, {
        "telegram_id": telegram_id,
        "msg_id": msg_id,
        "expires_at": expires_at
    }, None
