import os
import sqlite3
import datetime
import logging
from contextlib import contextmanager
from config import DATABASE_URL, LOGGER, USE_SUPABASE, SUPABASE_URL, SUPABASE_KEY

logger = LOGGER("Database")

# Detect DB type & configure PostgreSQL URL (NEVER default to localhost:5432!)
POSTGRES_URL = os.environ.get("POSTGRES_URL") or (
    DATABASE_URL if (DATABASE_URL and not DATABASE_URL.startswith("sqlite"))
    else None
)

# Local SQLite fallback
SQLITE_DB_FILE = (
    DATABASE_URL.replace("sqlite:///", "").replace("sqlite://", "")
    if DATABASE_URL.startswith("sqlite")
    else "cds.db"
)
if not SQLITE_DB_FILE:
    SQLITE_DB_FILE = "cds.db"


class PostgresConnectionPool:
    """Thread-safe connection pool with automatic healing and reconnect."""
    def __init__(self, dsn: str | None, minconn: int = 1, maxconn: int = 10):
        self.dsn = dsn
        self.minconn = minconn
        self.maxconn = maxconn
        self._pool = None
        if self.dsn:
            self._init_pool()

    def _init_pool(self):
        if not self.dsn:
            return
        try:
            import psycopg2
            import psycopg2.pool
            self._pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=self.minconn,
                maxconn=self.maxconn,
                dsn=self.dsn
            )
            logger.info("Initialized PostgreSQL connection pool successfully.")
        except Exception as e:
            logger.warning(f"PostgreSQL connection pool init failed: {e}")
            self._pool = None

    def getconn(self):
        if not self.dsn:
            return None
        if self._pool is None:
            self._init_pool()
        if self._pool is None:
            import psycopg2
            return psycopg2.connect(self.dsn)
        return self._pool.getconn()

    def putconn(self, conn, close: bool = False):
        if conn is None:
            return
        if self._pool is not None and not close:
            try:
                self._pool.putconn(conn)
                return
            except Exception:
                pass
        try:
            conn.close()
        except Exception:
            pass


_pg_pool = PostgresConnectionPool(POSTGRES_URL)


@contextmanager
def get_pg_cursor(commit: bool = False):
    """Context manager yielding a PostgreSQL RealDictCursor with auto-commit/rollback."""
    if not POSTGRES_URL:
        raise RuntimeError("PostgreSQL is not configured")
    conn = None
    try:
        from psycopg2.extras import RealDictCursor
        conn = _pg_pool.getconn()
        if not conn:
            raise RuntimeError("Could not acquire connection from pool")
        cur = conn.cursor(cursor_factory=RealDictCursor)
        yield cur
        if commit:
            conn.commit()
    except Exception as e:
        if conn and commit:
            try:
                conn.rollback()
            except Exception:
                pass
        raise e
    finally:
        if conn:
            _pg_pool.putconn(conn)


def get_sqlite_connection():
    """Fallback SQLite connection."""
    conn = sqlite3.connect(SQLITE_DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


# ============================================================================
# Supabase Cloud REST Helpers
# ============================================================================

def _get_supabase_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }


# Persistent aiohttp session — reused across all Supabase REST calls to avoid
# per-call TCP handshake overhead (~50-80ms saved per request).
_sb_session: "aiohttp.ClientSession | None" = None

async def _get_sb_session():
    """Return the shared aiohttp session, creating it lazily."""
    global _sb_session
    try:
        import aiohttp
        if _sb_session is None or _sb_session.closed:
            _sb_session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(limit=20, ttl_dns_cache=300)
            )
    except Exception:
        pass
    return _sb_session


async def _sb_get(table: str, params: dict = None) -> list[dict]:
    if not (USE_SUPABASE and SUPABASE_URL and SUPABASE_KEY):
        return []
    try:
        import aiohttp
        url = f"{SUPABASE_URL}/rest/v1/{table}"
        headers = _get_supabase_headers()
        session = await _get_sb_session()
        if session:
            async with session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    logger.debug(f"Supabase GET {table} status {resp.status}")
        else:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        return await resp.json()
    except Exception as e:
        logger.debug(f"Supabase GET {table} exception: {e}")
    return []


async def _sb_post(table: str, data: dict, upsert: bool = True) -> list[dict]:
    if not (USE_SUPABASE and SUPABASE_URL and SUPABASE_KEY):
        return []
    try:
        import aiohttp
        url = f"{SUPABASE_URL}/rest/v1/{table}"
        headers = _get_supabase_headers()
        if upsert:
            headers["Prefer"] = "resolution=merge-duplicates,return=representation"
        session = await _get_sb_session()
        async def _post(s):
            async with s.post(url, json=data, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status in (200, 201):
                    return await resp.json()
                else:
                    logger.debug(f"Supabase POST {table} status {resp.status}")
            return []
        if session:
            return await _post(session)
        async with aiohttp.ClientSession() as s:
            return await _post(s)
    except Exception as e:
        logger.debug(f"Supabase POST {table} exception: {e}")
    return []


async def _sb_patch(table: str, params: dict, data: dict) -> bool:
    if not (USE_SUPABASE and SUPABASE_URL and SUPABASE_KEY):
        return False
    try:
        import aiohttp
        url = f"{SUPABASE_URL}/rest/v1/{table}"
        headers = _get_supabase_headers()
        session = await _get_sb_session()
        async def _patch(s):
            async with s.patch(url, params=params, json=data, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                return resp.status in (200, 204)
        if session:
            return await _patch(session)
        async with aiohttp.ClientSession() as s:
            return await _patch(s)
    except Exception as e:
        logger.debug(f"Supabase PATCH {table} exception: {e}")
    return False


def _sb_get_sync(table: str, params: dict = None) -> list[dict]:
    if not (USE_SUPABASE and SUPABASE_URL and SUPABASE_KEY):
        return []
    try:
        import requests
        url = f"{SUPABASE_URL}/rest/v1/{table}"
        headers = _get_supabase_headers()
        r = requests.get(url, params=params, headers=headers, timeout=5)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logger.debug(f"Supabase sync GET {table} exception: {e}")
    return []


def init_db():
    """Initializes local SQLite database and verifies cloud connectivity."""
    # 1. Initialize local SQLite tables
    try:
        conn = get_sqlite_connection()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS enrollments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id BIGINT NOT NULL,
                batch_id TEXT NOT NULL,
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(telegram_id, batch_id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_ref TEXT UNIQUE NOT NULL,
                telegram_id BIGINT NOT NULL,
                batch_id TEXT NOT NULL,
                amount INTEGER NOT NULL,
                submitted_utr TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS consumed_tokens (
                token TEXT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                msg_id INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                consumed_at INTEGER NOT NULL
            )
        """)
        conn.commit()
        conn.close()
        logger.info(f"Local SQLite database initialized at: {SQLITE_DB_FILE}")
    except Exception as e:
        logger.error(f"Error initializing SQLite DB: {e}")


def is_token_consumed_persistent(token: str) -> bool:
    """Checks if a single-use token was previously consumed in persistent storage."""
    try:
        conn = get_sqlite_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM consumed_tokens WHERE token = ?", (token,))
        row = cur.fetchone()
        conn.close()
        return bool(row)
    except Exception as e:
        logger.error(f"Error checking consumed_tokens: {e}")
        return False


def mark_token_consumed_persistent(token: str, user_id: int, msg_id: int, expires_at: int) -> bool:
    """
    Atomically records a consumed token into the persistent database.
    Returns True if successfully recorded (first consumption).
    Returns False if already consumed (IntegrityError unique constraint).
    """
    now = int(datetime.datetime.now().timestamp())
    try:
        conn = get_sqlite_connection()
        cur = conn.cursor()
        # Auto-prune tokens expired more than 10 minutes ago
        cur.execute("DELETE FROM consumed_tokens WHERE expires_at < ?", (now - 600,))
        cur.execute(
            "INSERT INTO consumed_tokens (token, user_id, msg_id, expires_at, consumed_at) VALUES (?, ?, ?, ?, ?)",
            (token, user_id, msg_id, expires_at, now)
        )
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        return False
    except Exception as e:
        logger.error(f"Error marking token consumed: {e}")
        return True

    # 2. Check Supabase Cloud connectivity
    if USE_SUPABASE and SUPABASE_URL and SUPABASE_KEY:
        try:
            test = _sb_get_sync("video_assets", {"select": "id", "limit": "1"})
            if test is not None:
                logger.info("Connected to Supabase Cloud REST successfully.")
        except Exception as e:
            logger.warning(f"Could not connect to Supabase Cloud REST: {e}")

    # 3. Check PostgreSQL connectivity (only if explicitly configured)
    if POSTGRES_URL:
        try:
            with get_pg_cursor() as cur:
                cur.execute("SELECT 1 AS alive")
                row = cur.fetchone()
                if row and row["alive"] == 1:
                    logger.info("Connected to PostgreSQL database successfully.")
        except Exception as e:
            logger.warning(f"Could not connect to PostgreSQL on startup ({e}). Falling back to Supabase/SQLite.")


async def is_enrolled(telegram_id: int | str, batch_id: str) -> bool:
    """
    Checks if a user is actively enrolled in a batch.
    Queries Supabase Cloud first (enrollments, telegram_links, payment_tokens),
    then direct PostgreSQL if configured, falling back to local SQLite enrollments.
    """
    if not batch_id or str(batch_id).lower() in ["free", "demo", "test"]:
        return True

    tid_int = None
    try:
        tid_int = int(telegram_id)
    except (ValueError, TypeError):
        pass
    uid_str = str(telegram_id)

    # Master Admin Bypass: Admins have unconditional access to ALL batches & lectures
    ADMIN_IDS = [2131377350, 7507697121]
    if tid_int in ADMIN_IDS or uid_str in [str(x) for x in ADMIN_IDS]:
        return True

    # 1. Try Supabase Cloud
    if USE_SUPABASE and SUPABASE_URL and SUPABASE_KEY:
        try:
            # 1a. Check public.telegram_links -> public.enrollments
            if tid_int:
                links = await _sb_get("telegram_links", {"telegram_chat_id": f"eq.{tid_int}", "select": "user_id"})
                if links and links[0].get("user_id"):
                    sb_uid = links[0]["user_id"]
                    enrs = await _sb_get("enrollments", {
                        "user_id": f"eq.{sb_uid}",
                        "batch_id": f"eq.{str(batch_id)}",
                        "status": "eq.active",
                        "select": "id"
                    })
                    if enrs:
                        return True

            # 1b. Check if user enrolled directly by UUID or string
            direct_enrs = await _sb_get("enrollments", {
                "batch_id": f"eq.{str(batch_id)}",
                "status": "eq.active",
                "select": "user_id"
            })
            for de in direct_enrs:
                if str(de.get("user_id")) in (uid_str, str(tid_int)):
                    return True

            # 1c. Check payment_tokens (website purchases via Razorpay / UPI)
            search_tid = str(tid_int or uid_str)
            tokens = await _sb_get("payment_tokens", {
                "user_telegram_id": f"eq.{search_tid}",
                "status": "in.(verified_paid,deposit_verified_paid)",
                "select": "note"
            })
            for tok in tokens:
                note = str(tok.get("note", ""))
                if str(batch_id) in note:
                    return True
        except Exception as e:
            logger.debug(f"Supabase is_enrolled check error: {e}")

    # 2. Try PostgreSQL (if configured)
    if POSTGRES_URL:
        try:
            with get_pg_cursor() as cur:
                cur.execute("""
                    SELECT 1 FROM public.enrollments e
                    LEFT JOIN public.telegram_links tl ON tl.user_id = e.user_id
                    WHERE (
                        (tl.telegram_chat_id = %s)
                        OR (e.user_id::text = %s)
                    )
                    AND e.batch_id = %s
                    AND e.status = 'active'
                    LIMIT 1;
                """, (tid_int, uid_str, str(batch_id)))
                row = cur.fetchone()
                if row:
                    return True
        except Exception as e:
            logger.debug(f"PG is_enrolled check error: {e}")

    # 3. Fallback to SQLite
    try:
        conn = get_sqlite_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM enrollments WHERE (telegram_id = ? OR telegram_id = ?) AND batch_id = ? AND status = 'active'",
            (tid_int or 0, uid_str, str(batch_id))
        )
        row = cur.fetchone()
        conn.close()
        return bool(row)
    except Exception as e:
        logger.debug(f"SQLite is_enrolled check error: {e}")

    return False


async def add_enrollment(telegram_id: int | str, batch_id: str, status: str = "active"):
    """
    Adds or updates an active enrollment in Supabase Cloud, PostgreSQL, and SQLite.
    """
    tid_int = None
    try:
        tid_int = int(telegram_id)
    except (ValueError, TypeError):
        pass
    uid_str = str(telegram_id)

    # 1. Supabase Cloud Sync
    if USE_SUPABASE and SUPABASE_URL and SUPABASE_KEY:
        try:
            import uuid
            # Ensure batch exists in public.batches
            await _sb_post("batches", {"id": str(batch_id), "title": f"Batch {batch_id}", "is_active": True}, upsert=True)

            user_id = None
            if tid_int:
                links = await _sb_get("telegram_links", {"telegram_chat_id": f"eq.{tid_int}", "select": "user_id"})
                if links and links[0].get("user_id"):
                    user_id = links[0]["user_id"]
                else:
                    new_uid = str(uuid.uuid4())
                    await _sb_post("users", {"id": new_uid, "full_name": f"Telegram User {tid_int}", "role": "student"}, upsert=True)
                    await _sb_post("telegram_links", {"user_id": new_uid, "telegram_chat_id": tid_int, "first_name": f"Telegram User {tid_int}"}, upsert=True)
                    user_id = new_uid
            else:
                user_id = uid_str

            if user_id:
                await _sb_post("enrollments", {"user_id": user_id, "batch_id": str(batch_id), "status": status}, upsert=True)
                logger.info(f"Supabase enrollment added: user {user_id} -> batch {batch_id} ({status})")
        except Exception as e:
            logger.warning(f"Supabase add_enrollment error: {e}")

    # 2. PostgreSQL sync (if configured)
    if POSTGRES_URL:
        try:
            with get_pg_cursor(commit=True) as cur:
                cur.execute("""
                    INSERT INTO public.batches (id, title, is_active)
                    VALUES (%s, %s, TRUE)
                    ON CONFLICT (id) DO NOTHING;
                """, (str(batch_id), f"Batch {batch_id}"))

                user_id = None
                if tid_int:
                    cur.execute("SELECT user_id FROM public.telegram_links WHERE telegram_chat_id = %s", (tid_int,))
                    link_row = cur.fetchone()
                    if link_row:
                        user_id = link_row["user_id"]
                    else:
                        cur.execute("""
                            INSERT INTO public.users (id, full_name, role)
                            VALUES (gen_random_uuid(), %s, 'student')
                            RETURNING id;
                        """, (f"Telegram User {tid_int}",))
                        user_id = cur.fetchone()["id"]
                        cur.execute("""
                            INSERT INTO public.telegram_links (user_id, telegram_chat_id, first_name)
                            VALUES (%s, %s, %s)
                            ON CONFLICT (telegram_chat_id) DO NOTHING;
                        """, (user_id, tid_int, f"Telegram User {tid_int}"))
                else:
                    user_id = uid_str

                if user_id:
                    cur.execute("""
                        INSERT INTO public.enrollments (id, user_id, batch_id, status)
                        VALUES (gen_random_uuid(), %s, %s, %s)
                        ON CONFLICT (user_id, batch_id) DO UPDATE SET status = EXCLUDED.status;
                    """, (user_id, str(batch_id), status))
                    logger.info(f"PG enrollment added: user {user_id} -> batch {batch_id} ({status})")
        except Exception as e:
            logger.warning(f"PG add_enrollment error: {e}")

    # 3. SQLite sync
    if tid_int:
        try:
            conn = get_sqlite_connection()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO enrollments (telegram_id, batch_id, status)
                VALUES (?, ?, ?)
                ON CONFLICT(telegram_id, batch_id) DO UPDATE SET status = excluded.status
            """, (tid_int, str(batch_id), status))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"SQLite add_enrollment error: {e}")


async def revoke_enrollment(telegram_id: int | str, batch_id: str):
    """
    Revokes an enrollment in Supabase Cloud, PostgreSQL, and SQLite.
    """
    tid_int = None
    try:
        tid_int = int(telegram_id)
    except (ValueError, TypeError):
        pass
    uid_str = str(telegram_id)

    # 1. Supabase Cloud
    if USE_SUPABASE and SUPABASE_URL and SUPABASE_KEY:
        try:
            if tid_int:
                links = await _sb_get("telegram_links", {"telegram_chat_id": f"eq.{tid_int}", "select": "user_id"})
                if links and links[0].get("user_id"):
                    user_id = links[0]["user_id"]
                    await _sb_patch("enrollments", {"user_id": f"eq.{user_id}", "batch_id": f"eq.{str(batch_id)}"}, {"status": "revoked"})
                    logger.info(f"Supabase enrollment revoked: user {user_id} -> batch {batch_id}")
        except Exception as e:
            logger.warning(f"Supabase revoke_enrollment error: {e}")

    # 2. PostgreSQL (if configured)
    if POSTGRES_URL:
        try:
            with get_pg_cursor(commit=True) as cur:
                user_id = None
                if tid_int:
                    cur.execute("SELECT user_id FROM public.telegram_links WHERE telegram_chat_id = %s", (tid_int,))
                    link_row = cur.fetchone()
                    if link_row:
                        user_id = link_row["user_id"]
                else:
                    user_id = uid_str

                if user_id:
                    cur.execute("""
                        UPDATE public.enrollments
                        SET status = 'revoked'
                        WHERE user_id = %s AND batch_id = %s;
                    """, (user_id, str(batch_id)))
                    logger.info(f"PG enrollment revoked: user {user_id} -> batch {batch_id}")
        except Exception as e:
            logger.warning(f"PG revoke_enrollment error: {e}")

    # 3. SQLite
    if tid_int:
        try:
            conn = get_sqlite_connection()
            cur = conn.cursor()
            cur.execute(
                "UPDATE enrollments SET status = 'revoked' WHERE telegram_id = ? AND batch_id = ?",
                (tid_int, str(batch_id))
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"SQLite revoke_enrollment error: {e}")


async def get_user_enrollments(telegram_id: int | str) -> list[dict]:
    """
    Returns list of enrollments for a user from Supabase Cloud, PostgreSQL, or SQLite.
    """
    tid_int = None
    try:
        tid_int = int(telegram_id)
    except (ValueError, TypeError):
        pass
    uid_str = str(telegram_id)

    results = []
    seen_batches = set()

    # 1. Supabase Cloud
    if USE_SUPABASE and SUPABASE_URL and SUPABASE_KEY:
        try:
            if tid_int:
                links = await _sb_get("telegram_links", {"telegram_chat_id": f"eq.{tid_int}", "select": "user_id"})
                if links and links[0].get("user_id"):
                    user_id = links[0]["user_id"]
                    enrs = await _sb_get("enrollments", {
                        "user_id": f"eq.{user_id}",
                        "select": "batch_id,status,created_at"
                    })
                    for e in enrs:
                        b_id = e.get("batch_id")
                        if b_id and b_id not in seen_batches:
                            seen_batches.add(b_id)
                            results.append({
                                "batch_id": b_id,
                                "status": e.get("status", "active"),
                                "created_at": e.get("created_at"),
                                "batch_title": f"Batch {b_id}"
                            })

            # Check payment_tokens
            search_tid = str(tid_int or uid_str)
            tokens = await _sb_get("payment_tokens", {
                "user_telegram_id": f"eq.{search_tid}",
                "status": "in.(verified_paid,deposit_verified_paid)",
                "select": "note,created_at"
            })
            for tok in tokens:
                note = tok.get("note", "")
                if note and note not in seen_batches:
                    seen_batches.add(note)
                    results.append({
                        "batch_id": note,
                        "status": "active",
                        "created_at": tok.get("created_at"),
                        "batch_title": note
                    })
        except Exception as e:
            logger.debug(f"Supabase get_user_enrollments error: {e}")

    # 2. PostgreSQL (if configured)
    if POSTGRES_URL:
        try:
            with get_pg_cursor() as cur:
                cur.execute("""
                    SELECT e.batch_id, e.status, e.created_at, b.title as batch_title
                    FROM public.enrollments e
                    LEFT JOIN public.batches b ON b.id = e.batch_id
                    LEFT JOIN public.telegram_links tl ON tl.user_id = e.user_id
                    WHERE (tl.telegram_chat_id = %s OR e.user_id::text = %s);
                """, (tid_int, uid_str))
                rows = cur.fetchall()
                for r in rows:
                    b_id = r["batch_id"]
                    if b_id and b_id not in seen_batches:
                        seen_batches.add(b_id)
                        results.append(dict(r))
        except Exception as e:
            logger.debug(f"PG get_user_enrollments error: {e}")

    # 3. Fallback to SQLite
    try:
        conn = get_sqlite_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT batch_id, status, created_at FROM enrollments WHERE telegram_id = ?",
            (tid_int or 0,)
        )
        rows = cur.fetchall()
        conn.close()
        for r in rows:
            b_id = r["batch_id"]
            if b_id and b_id not in seen_batches:
                seen_batches.add(b_id)
                results.append(dict(r))
    except Exception:
        pass

    return results


async def get_batch_for_message(msg_id: int) -> str | None:
    """
    Looks up batch_id associated with a tg_message_id.
    Queries Supabase Cloud first, then PostgreSQL, SQLite, and cds_assets.json.
    """
    if not msg_id:
        return None

    # 1. Try Supabase Cloud
    if USE_SUPABASE and SUPABASE_URL and SUPABASE_KEY:
        try:
            rows = await _sb_get("video_assets", {"tg_message_id": f"eq.{int(msg_id)}", "select": "batch_id", "limit": "1"})
            if rows and rows[0].get("batch_id"):
                return rows[0]["batch_id"]
        except Exception as e:
            logger.debug(f"Supabase get_batch_for_message error: {e}")

    # 2. Try PostgreSQL (if configured)
    if POSTGRES_URL:
        try:
            with get_pg_cursor() as cur:
                cur.execute("SELECT batch_id FROM public.video_assets WHERE tg_message_id = %s LIMIT 1", (int(msg_id),))
                row = cur.fetchone()
                if row and row["batch_id"]:
                    return row["batch_id"]
        except Exception as e:
            logger.debug(f"PG get_batch_for_message error: {e}")

    # 3. Fallback: SQLite
    candidates = [
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "EduVault Web", "cds_assets.db"),
        os.path.join(os.path.dirname(__file__), "..", "..", "ARYAN-TXT2LEECH__V2", "cds_assets.db"),
        os.path.join(os.path.dirname(__file__), "..", "cds_assets.db"),
        "cds_assets.db"
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                conn = sqlite3.connect(p)
                cur = conn.cursor()
                cur.execute("SELECT batch_id FROM video_assets WHERE tg_message_id = ?", (int(msg_id),))
                row = cur.fetchone()
                conn.close()
                if row and row[0]:
                    return row[0]
            except Exception:
                pass

    # 4. Fallback: cds_assets.json
    json_candidates = [
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "EduVault Web", "public", "cds_assets.json"),
        os.path.join(os.path.dirname(__file__), "..", "..", "EduVault Web", "cds_assets.json"),
        "cds_assets.json"
    ]
    for jp in json_candidates:
        if os.path.exists(jp):
            try:
                import json
                with open(jp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for lec_id, info in data.items():
                    q_map = info.get("qualities", {})
                    if info.get("tgMessageId") == msg_id or msg_id in q_map.values():
                        return info.get("batchId")
            except Exception:
                pass

    return None


async def get_lecture_details_by_msg_id(msg_id: int) -> dict:
    """
    Retrieves all available qualities, batch_id, title, topic, subject, and thumb_url
    for a lecture identified by any of its tg_message_ids.
    Queries Supabase Cloud first, then PostgreSQL, SQLite, and cds_assets.json.
    """
    if not msg_id:
        return {
            "found": False,
            "lecture_id": None,
            "batch_id": None,
            "title": None,
            "topic": None,
            "subject": None,
            "thumb_url": None,
            "qualities": {},
            "default_msg_id": msg_id
        }

    # 1. Try Supabase Cloud
    if USE_SUPABASE and SUPABASE_URL and SUPABASE_KEY:
        try:
            primary_rows = await _sb_get("video_assets", {
                "tg_message_id": f"eq.{int(msg_id)}",
                "select": "lecture_id,batch_id,quality,title,topic,subject,thumb_url",
                "limit": "1"
            })
            if primary_rows:
                primary = primary_rows[0]
                lecture_id = primary.get("lecture_id")
                batch_id = primary.get("batch_id")
                title = primary.get("title")
                topic = primary.get("topic")
                subject = primary.get("subject")
                thumb_url = primary.get("thumb_url")

                all_q = await _sb_get("video_assets", {
                    "lecture_id": f"eq.{lecture_id}",
                    "select": "quality,tg_message_id,title,topic,subject,thumb_url"
                })

                qualities = {}
                for r in all_q:
                    q_name = r.get("quality") or "720p"
                    if r.get("tg_message_id"):
                        qualities[q_name] = int(r["tg_message_id"])
                    if not title and r.get("title"): title = r["title"]
                    if not topic and r.get("topic"): topic = r["topic"]
                    if not subject and r.get("subject"): subject = r["subject"]
                    if not thumb_url and r.get("thumb_url"): thumb_url = r["thumb_url"]

                return {
                    "found": True,
                    "lecture_id": lecture_id,
                    "batch_id": batch_id,
                    "title": title,
                    "topic": topic,
                    "subject": subject,
                    "thumb_url": thumb_url,
                    "qualities": qualities if qualities else {"720p": msg_id},
                    "default_msg_id": msg_id
                }
        except Exception as e:
            logger.debug(f"Supabase get_lecture_details_by_msg_id error: {e}")

    # 2. Try PostgreSQL (if configured)
    if POSTGRES_URL:
        try:
            with get_pg_cursor() as cur:
                cur.execute("""
                    SELECT lecture_id, batch_id, quality, title, topic, subject, thumb_url
                    FROM public.video_assets
                    WHERE tg_message_id = %s
                    LIMIT 1;
                """, (int(msg_id),))
                primary_row = cur.fetchone()
                if primary_row:
                    lecture_id = primary_row["lecture_id"]
                    batch_id = primary_row["batch_id"]
                    title = primary_row["title"]
                    topic = primary_row["topic"]
                    subject = primary_row["subject"]
                    thumb_url = primary_row["thumb_url"]

                    cur.execute("""
                        SELECT quality, tg_message_id, title, topic, subject, thumb_url
                        FROM public.video_assets
                        WHERE lecture_id = %s;
                    """, (lecture_id,))
                    all_qualities = cur.fetchall()

                    qualities = {}
                    for r in all_qualities:
                        q_name = r["quality"] or "720p"
                        qualities[q_name] = int(r["tg_message_id"])
                        if not title and r["title"]: title = r["title"]
                        if not topic and r["topic"]: topic = r["topic"]
                        if not subject and r["subject"]: subject = r["subject"]
                        if not thumb_url and r["thumb_url"]: thumb_url = r["thumb_url"]

                    return {
                        "found": True,
                        "lecture_id": lecture_id,
                        "batch_id": batch_id,
                        "title": title,
                        "topic": topic,
                        "subject": subject,
                        "thumb_url": thumb_url,
                        "qualities": qualities if qualities else {"720p": msg_id},
                        "default_msg_id": msg_id
                    }
        except Exception as e:
            logger.debug(f"PG get_lecture_details_by_msg_id error: {e}")

    # 3. Fallback to SQLite
    db_candidates = [
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "EduVault Web", "cds_assets.db"),
        os.path.join(os.path.dirname(__file__), "..", "..", "ARYAN-TXT2LEECH__V2", "cds_assets.db"),
        os.path.join(os.path.dirname(__file__), "..", "cds_assets.db"),
        "cds_assets.db"
    ]
    for p in db_candidates:
        if os.path.exists(p):
            try:
                conn = sqlite3.connect(p)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute("""
                    SELECT lecture_id, batch_id, quality, title, topic, subject, thumb_url
                    FROM video_assets
                    WHERE tg_message_id = ?
                """, (msg_id,))
                primary_row = cur.fetchone()
                if primary_row:
                    lecture_id = primary_row["lecture_id"]
                    batch_id = primary_row["batch_id"]
                    title = primary_row["title"]
                    topic = primary_row["topic"]
                    subject = primary_row["subject"]
                    thumb_url = primary_row["thumb_url"]

                    cur.execute("""
                        SELECT quality, tg_message_id, title, topic, subject, thumb_url
                        FROM video_assets
                        WHERE lecture_id = ?
                    """, (lecture_id,))
                    rows = cur.fetchall()
                    conn.close()

                    qualities = {}
                    for r in rows:
                        q_name = r["quality"] or "720p"
                        qualities[q_name] = int(r["tg_message_id"])
                        if not title and r["title"]: title = r["title"]
                        if not topic and r["topic"]: topic = r["topic"]
                        if not subject and r["subject"]: subject = r["subject"]
                        if not thumb_url and r["thumb_url"]: thumb_url = r["thumb_url"]

                    return {
                        "found": True,
                        "lecture_id": lecture_id,
                        "batch_id": batch_id,
                        "title": title,
                        "topic": topic,
                        "subject": subject,
                        "thumb_url": thumb_url,
                        "qualities": qualities if qualities else {"720p": msg_id},
                        "default_msg_id": msg_id
                    }
                conn.close()
            except Exception as e:
                logger.debug(f"Error querying SQLite {p} for msg_id {msg_id}: {e}")

    # 4. Fallback: cds_assets.json
    json_candidates = [
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "EduVault Web", "public", "cds_assets.json"),
        os.path.join(os.path.dirname(__file__), "..", "..", "EduVault Web", "cds_assets.json"),
        "cds_assets.json"
    ]
    for jp in json_candidates:
        if os.path.exists(jp):
            try:
                import json
                with open(jp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for lec_id, info in data.items():
                    q_map = info.get("qualities", {})
                    if info.get("tgMessageId") == msg_id or msg_id in q_map.values():
                        return {
                            "found": True,
                            "lecture_id": lec_id,
                            "batch_id": info.get("batchId"),
                            "title": info.get("title"),
                            "topic": info.get("topic"),
                            "subject": info.get("subject"),
                            "thumb_url": info.get("thumbUrl"),
                            "qualities": q_map if q_map else {"720p": msg_id},
                            "default_msg_id": msg_id
                        }
            except Exception:
                pass

    return {
        "found": False,
        "lecture_id": None,
        "batch_id": None,
        "title": None,
        "topic": None,
        "subject": None,
        "thumb_url": None,
        "qualities": {"720p": msg_id},
        "default_msg_id": msg_id
    }


async def resolve_tg_message_id(lecture_id: str, quality: str = "720p") -> int | None:
    """
    Queries video_assets to resolve tg_message_id for given (lecture_id, quality),
    with fallback to other available qualities (720p -> 1080p -> 480p -> 360p -> pdf).
    """
    if not lecture_id:
        return None

    quality_key = (quality or "720p").lower()
    if not quality_key.endswith("p") and quality_key.isdigit():
        quality_key += "p"

    # 1. Try Supabase Cloud
    if USE_SUPABASE and SUPABASE_URL and SUPABASE_KEY:
        try:
            # Exact quality match
            exact = await _sb_get("video_assets", {
                "lecture_id": f"eq.{str(lecture_id)}",
                "quality": f"eq.{quality_key}",
                "select": "tg_message_id",
                "limit": "1"
            })
            if exact and exact[0].get("tg_message_id"):
                return int(exact[0]["tg_message_id"])

            # Fallback to any quality
            all_q = await _sb_get("video_assets", {
                "lecture_id": f"eq.{str(lecture_id)}",
                "select": "quality,tg_message_id"
            })
            if all_q:
                priority = {"720p": 1, "1080p": 2, "480p": 3, "360p": 4, "pdf": 5}
                sorted_q = sorted(all_q, key=lambda x: priority.get((x.get("quality") or "").lower(), 99))
                if sorted_q and sorted_q[0].get("tg_message_id"):
                    return int(sorted_q[0]["tg_message_id"])
        except Exception as e:
            logger.debug(f"Supabase resolve_tg_message_id error: {e}")

    # 2. Try PostgreSQL (if configured)
    if POSTGRES_URL:
        try:
            with get_pg_cursor() as cur:
                cur.execute("""
                    SELECT tg_message_id FROM public.video_assets
                    WHERE lecture_id = %s AND quality = %s
                    LIMIT 1;
                """, (str(lecture_id), quality_key))
                row = cur.fetchone()
                if row and row["tg_message_id"]:
                    return int(row["tg_message_id"])

                cur.execute("""
                    SELECT tg_message_id FROM public.video_assets
                    WHERE lecture_id = %s
                    ORDER BY CASE quality
                        WHEN '720p' THEN 1
                        WHEN '1080p' THEN 2
                        WHEN '480p' THEN 3
                        WHEN '360p' THEN 4
                        WHEN 'pdf' THEN 5
                        ELSE 6
                    END
                    LIMIT 1;
                """, (str(lecture_id),))
                fallback_row = cur.fetchone()
                if fallback_row and fallback_row["tg_message_id"]:
                    return int(fallback_row["tg_message_id"])
        except Exception as e:
            logger.debug(f"PG resolve_tg_message_id error: {e}")

    # 3. Fallback to SQLite
    db_candidates = [
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "EduVault Web", "cds_assets.db"),
        os.path.join(os.path.dirname(__file__), "..", "..", "ARYAN-TXT2LEECH__V2", "cds_assets.db"),
        os.path.join(os.path.dirname(__file__), "..", "cds_assets.db"),
        "cds_assets.db"
    ]
    for p in db_candidates:
        if os.path.exists(p):
            try:
                conn = sqlite3.connect(p)
                cur = conn.cursor()
                cur.execute(
                    "SELECT tg_message_id FROM video_assets WHERE lecture_id = ? AND quality = ?",
                    (str(lecture_id), quality_key)
                )
                row = cur.fetchone()
                if row and row[0]:
                    conn.close()
                    return int(row[0])

                cur.execute(
                    "SELECT tg_message_id FROM video_assets WHERE lecture_id = ? LIMIT 1",
                    (str(lecture_id),)
                )
                row = cur.fetchone()
                conn.close()
                if row and row[0]:
                    return int(row[0])
            except Exception:
                pass

    # 4. Fallback to cds_assets.json
    json_candidates = [
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "EduVault Web", "public", "cds_assets.json"),
        os.path.join(os.path.dirname(__file__), "..", "..", "EduVault Web", "cds_assets.json"),
        "cds_assets.json"
    ]
    for jp in json_candidates:
        if os.path.exists(jp):
            try:
                import json
                with open(jp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                lec_info = data.get(str(lecture_id))
                if lec_info:
                    q_map = lec_info.get("qualities", {})
                    if quality_key in q_map:
                        return int(q_map[quality_key])
                    if lec_info.get("tgMessageId"):
                        return int(lec_info["tgMessageId"])
                    if q_map:
                        return int(next(iter(q_map.values())))
            except Exception:
                pass

    return None


def resolve_tg_message_id_sync(lecture_id: str, quality: str = "720p") -> int | None:
    """Synchronous version of resolve_tg_message_id."""
    if not lecture_id:
        return None

    quality_key = (quality or "720p").lower()
    if not quality_key.endswith("p") and quality_key.isdigit():
        quality_key += "p"

    # 1. Try Supabase Cloud sync
    if USE_SUPABASE and SUPABASE_URL and SUPABASE_KEY:
        try:
            exact = _sb_get_sync("video_assets", {
                "lecture_id": f"eq.{str(lecture_id)}",
                "quality": f"eq.{quality_key}",
                "select": "tg_message_id",
                "limit": "1"
            })
            if exact and exact[0].get("tg_message_id"):
                return int(exact[0]["tg_message_id"])

            all_q = _sb_get_sync("video_assets", {
                "lecture_id": f"eq.{str(lecture_id)}",
                "select": "quality,tg_message_id"
            })
            if all_q:
                priority = {"720p": 1, "1080p": 2, "480p": 3, "360p": 4, "pdf": 5}
                sorted_q = sorted(all_q, key=lambda x: priority.get((x.get("quality") or "").lower(), 99))
                if sorted_q and sorted_q[0].get("tg_message_id"):
                    return int(sorted_q[0]["tg_message_id"])
        except Exception:
            pass

    # 2. Try PostgreSQL (if configured)
    if POSTGRES_URL:
        try:
            with get_pg_cursor() as cur:
                cur.execute("""
                    SELECT tg_message_id FROM public.video_assets
                    WHERE lecture_id = %s AND quality = %s
                    LIMIT 1;
                """, (str(lecture_id), quality_key))
                row = cur.fetchone()
                if row and row["tg_message_id"]:
                    return int(row["tg_message_id"])
        except Exception:
            pass

    # 3. Fallback to SQLite
    db_candidates = [
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "EduVault Web", "cds_assets.db"),
        os.path.join(os.path.dirname(__file__), "..", "..", "ARYAN-TXT2LEECH__V2", "cds_assets.db"),
        os.path.join(os.path.dirname(__file__), "..", "cds_assets.db"),
        "cds_assets.db"
    ]
    for p in db_candidates:
        if os.path.exists(p):
            try:
                conn = sqlite3.connect(p)
                cur = conn.cursor()
                cur.execute(
                    "SELECT tg_message_id FROM video_assets WHERE lecture_id = ? AND quality = ?",
                    (str(lecture_id), quality_key)
                )
                row = cur.fetchone()
                if row and row[0]:
                    conn.close()
                    return int(row[0])
                cur.execute(
                    "SELECT tg_message_id FROM video_assets WHERE lecture_id = ? LIMIT 1",
                    (str(lecture_id),)
                )
                row = cur.fetchone()
                conn.close()
                if row and row[0]:
                    return int(row[0])
            except Exception:
                pass

    return None


# Initialize on import
init_db()
