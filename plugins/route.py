import time
import os
import hmac
import hashlib
import aiohttp
from aiohttp import web

routes = web.RouteTableDef()

# In-memory store for active login sessions
# Format: session_id -> { "status": "pending" | "confirmed", "user": dict, "code": str, "created_at": int }
AUTH_SESSIONS = {}

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
}

def clean_expired_sessions():
    now = int(time.time())
    expired = [s for s, data in AUTH_SESSIONS.items() if now - data.get("created_at", 0) > 600]
    for s in expired:
        AUTH_SESSIONS.pop(s, None)

def register_auth_session(session_id: str, code: str):
    clean_expired_sessions()
    AUTH_SESSIONS[session_id] = {
        "status": "pending",
        "code": code,
        "created_at": int(time.time()),
        "user": None
    }

def confirm_auth_session(session_id: str, user_dict: dict):
    if session_id in AUTH_SESSIONS:
        AUTH_SESSIONS[session_id]["status"] = "confirmed"
        AUTH_SESSIONS[session_id]["user"] = user_dict
        return True
    return False

@routes.options("/{tail:.*}")
async def options_handler(request):
    return web.Response(status=204, headers=CORS_HEADERS)

@routes.get("/", allow_head=True)
async def root_route_handler(request):
    return web.json_response("CodeXBotz", headers=CORS_HEADERS)

@routes.get("/auth/status")
async def auth_status_handler(request):
    session_id = request.query.get("session")
    if not session_id:
        return web.json_response({"error": "Missing session id"}, status=400, headers=CORS_HEADERS)

    session = AUTH_SESSIONS.get(session_id)
    if not session:
        return web.json_response({"status": "not_found"}, headers=CORS_HEADERS)

    # Check 10-minute expiry
    if int(time.time()) - session.get("created_at", 0) > 600:
        AUTH_SESSIONS.pop(session_id, None)
        return web.json_response({"status": "expired"}, headers=CORS_HEADERS)

    return web.json_response({
        "status": session["status"],
        "user": session.get("user"),
        "code": session.get("code")
    }, headers=CORS_HEADERS)

@routes.post("/auth/verify-code")
async def verify_code_handler(request):
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400, headers=CORS_HEADERS)

    code = str(data.get("code", "")).strip()
    session_id = data.get("session", "")

    if not code:
        return web.json_response({"error": "Code required"}, status=400, headers=CORS_HEADERS)

    # Search for matching session by code or session_id
    matched_session = None
    for s_id, s_data in list(AUTH_SESSIONS.items()):
        if s_data.get("code") == code:
            if not session_id or s_id == session_id:
                matched_session = s_data
                break

    if matched_session and matched_session.get("status") == "confirmed":
        return web.json_response({
            "success": True,
            "user": matched_session.get("user")
        }, headers=CORS_HEADERS)

    return web.json_response({
        "success": False,
        "error": "Invalid or unconfirmed code."
    }, status=400, headers=CORS_HEADERS)

# ── RAZORPAY STANDARD CHECKOUT BACKEND ENDPOINTS ──
RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")

@routes.post("/api/create-order")
async def create_order_handler(request):
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400, headers=CORS_HEADERS)

    amount = data.get("amount")
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        return web.json_response({"error": "Invalid amount"}, status=400, headers=CORS_HEADERS)

    if amount < 100:
        return web.json_response({"error": "Amount must be at least 100 paise"}, status=400, headers=CORS_HEADERS)

    currency = data.get("currency", "INR")
    receipt = data.get("receipt", f"rcpt_{int(time.time())}")

    auth = aiohttp.BasicAuth(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET)
    payload = {
        "amount": amount,
        "currency": currency,
        "receipt": receipt,
        "notes": data.get("notes", {})
    }

    try:
        async with aiohttp.ClientSession(auth=auth) as session:
            async with session.post("https://api.razorpay.com/v1/orders", json=payload) as resp:
                result = await resp.json()
                if resp.status in (200, 201):
                    return web.json_response({
                        "order_id": result.get("id"),
                        "amount": result.get("amount"),
                        "currency": result.get("currency")
                    }, headers=CORS_HEADERS)
                elif resp.status == 401:
                    return web.json_response({"error": "Razorpay authentication failed"}, status=401, headers=CORS_HEADERS)
                else:
                    return web.json_response({
                        "error": result.get("error", {}).get("description", "Failed to create Razorpay order")
                    }, status=resp.status, headers=CORS_HEADERS)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500, headers=CORS_HEADERS)

@routes.post("/api/verify-payment")
async def verify_payment_handler(request):
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400, headers=CORS_HEADERS)

    order_id = data.get("razorpay_order_id")
    payment_id = data.get("razorpay_payment_id")
    signature = data.get("razorpay_signature")

    if not order_id or not payment_id or not signature:
        return web.json_response({
            "success": False,
            "error": "Missing required verification fields: razorpay_order_id, razorpay_payment_id, or razorpay_signature"
        }, status=400, headers=CORS_HEADERS)

    msg = f"{order_id}|{payment_id}".encode()
    gen_signature = hmac.new(RAZORPAY_KEY_SECRET.encode(), msg, hashlib.sha256).hexdigest()

    if hmac.compare_digest(gen_signature, signature):
        return web.json_response({
            "success": True,
            "message": "Payment verified successfully",
            "order_id": order_id,
            "payment_id": payment_id
        }, headers=CORS_HEADERS)
    else:
        return web.json_response({
            "success": False,
            "error": "Payment signature verification failed"
        }, status=400, headers=CORS_HEADERS)

