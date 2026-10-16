import os
import re
import asyncio
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait

from bot import Bot
from config import ADMINS, START_MSG, PROTECT_CONTENT, START_PIC, CHANNEL_ID
from token_helper import verify_and_consume_token, generate_token
from database.database import (
    is_enrolled,
    add_enrollment,
    revoke_enrollment,
    get_user_enrollments,
    get_batch_for_message,
    get_lecture_details_by_msg_id,
    resolve_tg_message_id
)


def make_cinematic_thumbnail(image_path: str) -> str:
    """
    Transforms any square or portrait thumbnail into a sleek 16:9 cinematic video banner
    with a softly blurred ambient background. Prevents awkward tall vertical image stretching in Telegram.
    """
    try:
        from PIL import Image, ImageFilter, ImageEnhance
        with Image.open(image_path) as img:
            img = img.convert("RGB")
            orig_w, orig_h = img.size
            aspect = orig_w / orig_h

            # If already widescreen (between 1.55 and 1.85), keep as is
            if 1.55 <= aspect <= 1.85:
                return image_path

            target_w = 1280
            target_h = 720

            # Background: scale to cover 1280x720, crop, blur, and darken for ambient glow
            bg_scale = max(target_w / orig_w, target_h / orig_h)
            bg_w = int(orig_w * bg_scale)
            bg_h = int(orig_h * bg_scale)
            bg = img.resize((bg_w, bg_h), Image.Resampling.LANCZOS)
            left = (bg_w - target_w) // 2
            top = (bg_h - target_h) // 2
            bg = bg.crop((left, top, left + target_w, top + target_h))
            bg = bg.filter(ImageFilter.GaussianBlur(radius=28))
            bg = ImageEnhance.Brightness(bg).enhance(0.40)

            # Foreground: scale to fit neatly inside canvas with a small border
            fg_scale = min((target_w - 60) / orig_w, (target_h - 40) / orig_h)
            fg_w = int(orig_w * fg_scale)
            fg_h = int(orig_h * fg_scale)
            fg = img.resize((fg_w, fg_h), Image.Resampling.LANCZOS)

            offset_x = (target_w - fg_w) // 2
            offset_y = (target_h - fg_h) // 2
            bg.paste(fg, (offset_x, offset_y))

            cinematic_path = image_path + "_16_9.jpg"
            bg.save(cinematic_path, "JPEG", quality=92)
            return cinematic_path
    except Exception as e:
        print(f"[CDS BOT] Note: make_cinematic_thumbnail fallback: {e}", flush=True)
        return image_path


@Bot.on_message(filters.command('start') & filters.private)
async def start_command(client: Client, message: Message):
    text = message.text.strip()
    user_id = message.from_user.id
    user_name = f"{message.from_user.first_name} {message.from_user.last_name or ''}".strip()
    username_str = f"@{message.from_user.username}" if message.from_user.username else "(no username)"

    print(f"\n{'='*70}", flush=True)
    print(f"[CDS BOT EVENT] >>> INCOMING /start REQUEST <<<", flush=True)
    print(f"[CDS BOT EVENT] Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    print(f"[CDS BOT EVENT] From: {user_name} | Username: {username_str} | ID: {user_id}", flush=True)
    print(f"[CDS BOT EVENT] Raw Text: {repr(text)}", flush=True)

    # 1. Check if start payload exists (e.g. /start tok_XXXX or /start login_XXXX)
    if " " in text:
        token_str = text.split(" ", 1)[1].strip()
        print(f"[CDS BOT EVENT] Found Payload: {token_str} (Length: {len(token_str)} chars)", flush=True)

        # 1A. Direct Bot Login Handler
        if token_str.startswith("login_") or token_str.startswith("auth_"):
            session_id = token_str.replace("login_", "").replace("auth_", "")
            print(f"[CDS BOT EVENT] 🔑 Direct Bot Login for session: {session_id}", flush=True)

            user_dict = {
                "id": user_id,
                "first_name": message.from_user.first_name,
                "last_name": message.from_user.last_name or "",
                "username": message.from_user.username or "",
                "photo_url": "",
                "auth_date": int(datetime.now().timestamp())
            }

            from .route import confirm_auth_session, register_auth_session, AUTH_SESSIONS
            import random

            code = str(random.randint(100000, 999999))
            if session_id not in AUTH_SESSIONS:
                register_auth_session(session_id, code)
            else:
                code = AUTH_SESSIONS[session_id].get("code", code)

            confirm_auth_session(session_id, user_dict)
            print(f"[CDS BOT EVENT] ✅ Session {session_id} confirmed for user {user_id} ({user_name})", flush=True)

            # Distributed Cloud Sync: Save confirmed session to Supabase token_events
            try:
                import json
                import requests
                from config import USE_SUPABASE, SUPABASE_URL, SUPABASE_KEY
                if USE_SUPABASE and SUPABASE_URL and SUPABASE_KEY:
                    sb_endpoint = f"{SUPABASE_URL.rstrip('/')}/rest/v1/token_events"
                    sb_headers = {
                        "apikey": SUPABASE_KEY,
                        "Authorization": f"Bearer {SUPABASE_KEY}",
                        "Content-Type": "application/json",
                        "Prefer": "return=minimal"
                    }
                    sb_payload = {
                        "token": session_id,
                        "user_id": str(user_id),
                        "username": message.from_user.username or "",
                        "event_type": "auth_login",
                        "metadata": json.dumps(user_dict)
                    }
                    resp = requests.post(sb_endpoint, headers=sb_headers, json=sb_payload, timeout=5)
                    print(f"[CDS BOT EVENT] 🚀 Supabase Cloud Auth Event published for session: {session_id} (Status: {resp.status_code})", flush=True)
            except Exception as e_sb:
                print(f"[CDS BOT EVENT] Supabase Auth Event notice: {e_sb}", flush=True)

            await message.reply_text(
                f"👋 <b>Welcome {message.from_user.first_name}!</b>\n\n"
                f"✅ <b>EduVault Login Confirmed</b>\n"
                f"Account: <b>{user_name}</b> (ID: <code>{user_id}</code>)\n\n"
                f"Your browser has been authenticated and will sign in automatically.",
                quote=True,
                parse_mode=ParseMode.HTML
            )
            return

        if token_str.startswith("tok_"):
            print(f"[CDS BOT EVENT] Starting Token Verification...", flush=True)
            # Validate token integrity, expiry, single-use cache, and identity match
            is_valid, data, error_msg = verify_and_consume_token(token_str, user_id)
            print(f"[CDS BOT EVENT] Verification Outcome: is_valid={is_valid}, data={data}, err={error_msg}", flush=True)

            if not is_valid:
                print(f"[CDS BOT EVENT] ⛔ ACCESS DENIED: {error_msg}", flush=True)
                print(f"{'='*70}\n", flush=True)
                await message.reply_text(
                    "<b>Access Authorization Notice</b>\n\n"
                    "<blockquote>"
                    f"{error_msg}"
                    "</blockquote>\n\n"
                    "💡 <i>Return to the EduVault dashboard to launch a fresh streaming session.</i>",
                    quote=True,
                    parse_mode=ParseMode.HTML
                )
                return

            msg_id = data.get("msg_id", 0)
            token_tg_id = data.get("telegram_id", -1)
            is_demo_token = (token_tg_id == 0)

            # ── INSTANT RESPONSE ──────────────────────────────────────────────
            # Reply immediately so user sees activity while we do DB + API work.
            loading_msg = await message.reply_text(
                "⏳ <b>Loading your lecture...</b>",
                quote=True,
                parse_mode=ParseMode.HTML
            )

            # ── PARALLEL RESOLUTION ───────────────────────────────────────────
            # Resolve lecture_id from msg_id if needed, then fan-out all slow
            # async operations concurrently instead of sequentially.
            if (not msg_id or msg_id == 0) and data.get("lecture_id"):
                resolved_msg_id = await resolve_tg_message_id(data["lecture_id"], data.get("quality", "720p"))
                if resolved_msg_id:
                    msg_id = resolved_msg_id
            print(f"[CDS BOT EVENT] ✅ TOKEN AUTHORIZED! Target Message ID: {msg_id}", flush=True)

            # Fan-out: batch_id lookup + lecture details + storage message – all at once
            async def _fetch_batch_id():
                if user_id not in ADMINS:
                    return await get_batch_for_message(msg_id)
                return None

            async def _fetch_storage_msg():
                try:
                    return await client.get_messages(chat_id=client.db_channel.id, message_ids=msg_id)
                except Exception as e_inspect:
                    print(f"[CDS BOT EVENT] Note: Could not pre-inspect message {msg_id}: {e_inspect}", flush=True)
                    return None

            batch_id_res, details, storage_msg = await asyncio.gather(
                _fetch_batch_id(),
                get_lecture_details_by_msg_id(msg_id),
                _fetch_storage_msg(),
            )

            # ── ENROLLMENT CHECK ──────────────────────────────────────────────
            print(f"[CDS BOT EVENT] Checking course enrollment for user {user_id}...", flush=True)
            if user_id not in ADMINS:
                batch_id = batch_id_res
                print(f"[CDS BOT EVENT] Message {msg_id} registered batch_id: {batch_id}", flush=True)
                if batch_id and batch_id.lower() not in ["free", "demo"]:
                    has_access = await is_enrolled(user_id, batch_id)
                    print(f"[CDS BOT EVENT] Enrollment status for batch {batch_id}: {has_access}", flush=True)
                    if not has_access:
                        print(f"[CDS BOT EVENT] ⛔ ACCESS DENIED: User {user_id} not enrolled in batch {batch_id}", flush=True)
                        print(f"{'='*70}\n", flush=True)
                        try:
                            await loading_msg.edit_text(
                                "<b>Course Access Required</b>\n\n"
                                "<blockquote>"
                                f"No active enrollment found for batch: <code>{batch_id}</code>.\n"
                                f"Account ID: <code>{user_id}</code>"
                                "</blockquote>\n\n"
                                "💡 <i>Please enroll in this course or link your account from the EduVault portal.</i>",
                                parse_mode=ParseMode.HTML
                            )
                        except Exception:
                            pass
                        return
                else:
                    print(f"[CDS BOT EVENT] Note: Free/demo or no batch constraint on message {msg_id}, allowing.", flush=True)
            else:
                print(f"[CDS BOT EVENT] User {user_id} is in ADMINS list -> Bypassing enrollment check.", flush=True)

            # ── LECTURE UNAVAILABLE CHECK ─────────────────────────────────────
            if storage_msg is None or storage_msg.empty:
                print(f"[CDS BOT EVENT] ⚠️ WARNING: Message {msg_id} in storage chat is EMPTY or DOES NOT EXIST!", flush=True)
                try:
                    await loading_msg.edit_text(
                        "⚠️ <b>Lecture Temporarily Unavailable</b>\n\n"
                        "This video is currently being updated or re-encoded on our server. "
                        "Please try again shortly or contact support.",
                        parse_mode=ParseMode.HTML
                    )
                except Exception:
                    pass
                return

            # ── EXTRACT METADATA ──────────────────────────────────────────────
            def _clean_caption_val(val: str) -> str:
                return val.replace("`", "").replace("<b>", "").replace("</b>", "").replace("**", "").replace("*", "").strip()

            extracted_title = None
            extracted_topic = None
            extracted_subject = None
            extracted_batch = None

            if storage_msg.caption:
                for line in storage_msg.caption.split("\n"):
                    line_clean = line.strip()
                    if any(b in line_clean for b in ["Batch:", "𝐁𝐚𝐭𝐜𝐡:", "📦"]) and ":" in line_clean:
                        extracted_batch = _clean_caption_val(line_clean.split(":", 1)[1])
                    elif any(s in line_clean for s in ["Subject:", "𝐒𝐮𝐛𝐣𝐞𝐜𝐭:", "📚"]) and ":" in line_clean:
                        extracted_subject = _clean_caption_val(line_clean.split(":", 1)[1])
                    elif any(t in line_clean for t in ["Topic:", "𝐓𝐨𝐩𝐢𝐜:", "📖", "🗂️"]) and ":" in line_clean:
                        extracted_topic = _clean_caption_val(line_clean.split(":", 1)[1])
                    elif any(t in line_clean for t in ["Title:", "𝐓𝐢𝐭𝐥𝐞:", "📹", "💿"]) and ":" in line_clean:
                        extracted_title = _clean_caption_val(line_clean.split(":", 1)[1])

            def _clean_title(raw: str) -> str:
                if not raw:
                    return "Lecture Video"
                t = raw.strip()
                t = re.sub(r'\.(mkv|mp4|webm|avi|flv|mov)$', '', t, flags=re.IGNORECASE)
                t = re.sub(r'\s+', ' ', t).strip()
                return t

            qualities = details.get("qualities") or {"720p": msg_id}
            raw_title = details.get("title") or extracted_title or (storage_msg.video.file_name if storage_msg.video else "Lecture Video")
            title = _clean_title(raw_title)
            topic = details.get("topic") or extracted_topic or "General Studies"
            subject = details.get("subject") or extracted_subject or "Curriculum"
            batch_name = extracted_batch or details.get("batch_name") or details.get("batch_id") or "EduVault Premium"

            # ── QUALITY BUTTONS ───────────────────────────────────────────────
            greeting_caption = (
                f"<b>{title}</b>\n\n"
                f"<blockquote>"
                f"<b>Batch</b>: {batch_name}\n"
                f"<b>Subject</b>: {subject}\n"
                f"<b>Module</b>: {topic}"
                f"</blockquote>\n\n"
                f"Select playback quality below to begin streaming."
            )

            buttons = []
            priority = {"1080p": 1, "720p": 2, "480p": 3, "360p": 4, "240p": 5}
            sorted_qualities = sorted(
                qualities.items(),
                key=lambda item: priority.get(item[0].lower(), 99)
            )

            # Deduplicate by message ID: never display duplicate buttons pointing to the same message
            seen_msg_ids = set()
            deduped_qualities = []
            for q_label, q_msg_id in sorted_qualities:
                if q_msg_id in seen_msg_ids:
                    continue
                seen_msg_ids.add(q_msg_id)
                deduped_qualities.append((q_label, q_msg_id))

            row = []
            for q_label, q_msg_id in deduped_qualities:
                q_upper = q_label.upper()
                if "720" in q_upper:
                    btn_text = f"? {q_upper} HD"
                elif "1080" in q_upper:
                    btn_text = f"? {q_upper} HD"
                elif "480" in q_upper:
                    btn_text = f"? {q_upper} SD"
                elif "360" in q_upper:
                    btn_text = f"? {q_upper} (Data Saver)"
                else:
                    btn_text = f"? {q_upper}"

                row.append(InlineKeyboardButton(btn_text, callback_data=f"stream_{q_msg_id}"))
                if len(row) == 2:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)

            reply_markup = InlineKeyboardMarkup(buttons)

            # ── SEND GREETING ─────────────────────────────────────────────────
            # Strategy: edit loading_msg with text+buttons FIRST (instant),
            # then try to send a separate photo card if a thumbnail is available.
            downloaded_thumb_file = None
            cinematic_thumb_file = None
            try:
                # Edit the placeholder immediately with quality buttons — user can act now
                await loading_msg.edit_text(
                    text=greeting_caption,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.HTML
                )

                # Then (in background) try to send a richer photo card
                thumb_photo = None
                if storage_msg.video and storage_msg.video.thumbs and len(storage_msg.video.thumbs) > 0:
                    try:
                        downloaded_thumb_file = await client.download_media(storage_msg.video.thumbs[0])
                        if downloaded_thumb_file and os.path.exists(downloaded_thumb_file):
                            cinematic_thumb_file = make_cinematic_thumbnail(downloaded_thumb_file)
                            thumb_photo = cinematic_thumb_file
                    except Exception as e_th:
                        print(f"[CDS BOT EVENT] Could not download/process video thumb: {e_th}", flush=True)
                elif storage_msg.photo:
                    thumb_photo = storage_msg.photo.file_id
                elif details.get("thumb_url"):
                    thumb_photo = details["thumb_url"]

                if thumb_photo:
                    try:
                        # Send photo card as a follow-up (loading_msg already has buttons so user isn't blocked)
                        await message.reply_photo(
                            photo=thumb_photo,
                            caption=greeting_caption,
                            reply_markup=reply_markup,
                            quote=True,
                            parse_mode=ParseMode.HTML
                        )
                        # Clean up the plain text loading message since photo card is nicer
                        try:
                            await loading_msg.delete()
                        except Exception:
                            pass
                    except Exception as e_photo:
                        print(f"[CDS BOT EVENT] Photo send failed ({e_photo}), keeping text card", flush=True)
            finally:
                for f_tmp in [downloaded_thumb_file, cinematic_thumb_file]:
                    if f_tmp and os.path.exists(f_tmp):
                        try:
                            os.remove(f_tmp)
                        except Exception:
                            pass
            return
        else:
            print(f"[CDS BOT EVENT] ⚠️ Warning: Start payload '{token_str}' does not start with 'tok_'. Falling back to default welcome.", flush=True)

    # 2. Default Start Message (No token provided)
    print(f"[CDS BOT] No valid token payload in text. Sending default welcome message.")
    reply_markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📚 My Enrollments", callback_data="my_enrollments"),
                InlineKeyboardButton("📞 Support", url="https://t.me/aryan_patel_99")
            ]
        ]
    )

    caption = START_MSG.format(
        first=message.from_user.first_name,
        last=message.from_user.last_name or "",
        username=f"@{message.from_user.username}" if message.from_user.username else "",
        mention=message.from_user.mention,
        id=user_id
    )

    if START_PIC:
        await message.reply_photo(
            photo=START_PIC,
            caption=caption,
            reply_markup=reply_markup,
            quote=True,
            parse_mode=ParseMode.HTML
        )
    else:
        await message.reply_text(
            text=caption,
            reply_markup=reply_markup,
            disable_web_page_preview=True,
            quote=True,
            parse_mode=ParseMode.HTML
        )


@Bot.on_callback_query(filters.regex(r"^stream_(\d+)$"))
async def handle_stream_callback(client: Client, callback_query):
    target_msg_id = int(callback_query.matches[0].group(1))
    user_id = callback_query.from_user.id
    user_name = f"{callback_query.from_user.first_name} {callback_query.from_user.last_name or ''}".strip()

    print(f"\n[CDS BOT EVENT] Quality stream callback triggered: target_msg_id={target_msg_id} by user={user_id} ({user_name})", flush=True)

    try:
        await callback_query.answer("⚡ Sending your lecture...", show_alert=False)
    except Exception as e_ans:
        print(f"[CDS BOT EVENT] Note: callback_query.answer notice: {e_ans}", flush=True)

    # Check enrollment if user not admin (Demo/Free lectures are always accessible)
    if user_id not in ADMINS:
        batch_id = await get_batch_for_message(target_msg_id)
        details = await get_lecture_details_by_msg_id(target_msg_id)
        topic = (details.get("topic") or "").lower()
        title = (details.get("title") or "").lower()
        is_demo = "demo" in topic or "demo" in title or (batch_id and str(batch_id).lower() in ["free", "demo", "test"])

        if not is_demo and batch_id and not await is_enrolled(user_id, batch_id):
            await callback_query.message.reply_text(
                "🔒 <b>Course Access Required</b>\n\nYou do not have an active enrollment for this batch.",
                quote=True,
                parse_mode=ParseMode.HTML
            )
            return

    # Deliver the protected message from the storage channel
    status_msg = None
    try:
        status_msg = await callback_query.message.reply_text("⚡ <b>Delivering video to your DM...</b>", parse_mode=ParseMode.HTML)
    except Exception as e_stat:
        print(f"[CDS BOT EVENT] Note: Could not send status msg: {e_stat}", flush=True)

    try:
        copied_msg = await client.copy_message(
            chat_id=user_id,
            from_chat_id=client.db_channel.id,
            message_id=target_msg_id,
            protect_content=PROTECT_CONTENT
        )
        print(f"[CDS BOT EVENT] 🎉 SUCCESS: Video {target_msg_id} delivered to user {user_id}! (DM Msg ID: {copied_msg.id})", flush=True)
        if status_msg:
            try:
                await status_msg.delete()
            except Exception:
                pass
    except FloodWait as e:
        print(f"[CDS BOT EVENT] FloodWait encountered: sleeping for {e.value}s...", flush=True)
        await asyncio.sleep(e.value)
        copied_msg = await client.copy_message(
            chat_id=user_id,
            from_chat_id=client.db_channel.id,
            message_id=target_msg_id,
            protect_content=PROTECT_CONTENT
        )
        if status_msg:
            try:
                await status_msg.delete()
            except Exception:
                pass
    except Exception as e:
        err_str = str(e)
        is_ghost_asset = "MESSAGE_ID_INVALID" in err_str or "MessageIdInvalid" in type(e).__name__ or "MESSAGE_EMPTY" in err_str

        if is_ghost_asset:
            print(f"[CDS BOT EVENT] ⚠️ GHOST CDN ASSET: Storage message {target_msg_id} was deleted/invalidated from channel!", flush=True)
            # Edge Case #9: Flag ghost asset in local SQLite database so ingestion bot re-uploads it
            try:
                import sqlite3
                for db_file in ("cds_assets.db", "cds.db"):
                    if os.path.exists(db_file):
                        with sqlite3.connect(db_file) as conn:
                            conn.execute("UPDATE lecture_assets SET tg_message_id = 0 WHERE tg_message_id = ?", (target_msg_id,))
                            conn.commit()
            except Exception as e_db:
                print(f"[CDS BOT EVENT] Warning: Could not purge ghost asset from db: {e_db}", flush=True)

            ghost_card = (
                "<blockquote>"
                "⚠️ <b>Asset Unavailable (CDN Re-indexing Required)</b>\n\n"
                "This video file has been archived or refreshed on the storage cluster.\n\n"
                f"• <b>CDN Ref:</b> <code>{target_msg_id}</code>\n"
                "• <b>Status:</b> Automatically flagged for re-ingestion."
                "</blockquote>"
            )
            if status_msg:
                try:
                    await status_msg.edit_text(ghost_card, parse_mode=ParseMode.HTML)
                except Exception:
                    pass
            else:
                try:
                    await callback_query.message.reply_text(ghost_card, parse_mode=ParseMode.HTML)
                except Exception:
                    pass
            return

        import traceback
        tb = traceback.format_exc()
        print(f"[CDS BOT EVENT] ❌ Delivery failed:\n{tb}", flush=True)
        if status_msg:
            try:
                await status_msg.edit_text(
                    f"<blockquote>❌ <b>Delivery failed:</b>\n<code>{str(e)}</code></blockquote>",
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass
        else:
            try:
                await callback_query.message.reply_text(
                    f"<blockquote>❌ <b>Delivery failed:</b>\n<code>{str(e)}</code></blockquote>",
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass


# ==========================================
# Admin Helper Commands
# ==========================================

@Bot.on_message(filters.command('testlink') & filters.private & filters.user(ADMINS))
async def generate_test_link(client: Client, message: Message):
    """
    Usage: /testlink <msg_id> [batch_id]
    Generates a 90s test token link for the admin to click and test instant playback.
    """
    args = message.text.split()
    if len(args) < 2:
        await message.reply_text("<b>Usage:</b> <code>/testlink &lt;msg_id&gt; [batch_id]</code>", parse_mode=ParseMode.HTML)
        return

    try:
        msg_id = int(args[1])
    except ValueError:
        await message.reply_text("❌ <code>msg_id</code> must be an integer.")
        return

    batch_id = args[2] if len(args) > 2 else "demo-batch"
    user_id = message.from_user.id

    token = generate_token(telegram_id=user_id, batch_id=batch_id, msg_id=msg_id, expires_in_seconds=90)
    link = f"https://t.me/{client.username}?start={token}"

    await message.reply_text(
        f"🔗 <b>90-Second Single-Use Test Link:</b>\n\n"
        f"<b>Target Msg ID:</b> <code>{msg_id}</code>\n"
        f"<b>Batch:</b> <code>{batch_id}</code>\n"
        f"<b>Valid for:</b> 90 seconds\n\n"
        f"👉 <a href='{link}'>Click here to test delivery</a>\n\n"
        f"<code>{link}</code>",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )


@Bot.on_message(filters.command('sync') & filters.private & filters.user(ADMINS))
async def sync_channel_cmd(client: Client, message: Message):
    """
    Usage: /sync
    Audits all registered assets against the Telegram Channel.
    Purges any deleted/missing videos from the database and updates the website.
    """
    import json
    import sqlite3
    status = await message.reply_text("🔍 <b>Auditing Storage Channel & Database...</b>", parse_mode=ParseMode.HTML)
    
    web_json = r"D:\Projects\EduVault Web\public\cds_assets.json"
    web_db = r"D:\Projects\EduVault Web\cds_assets.db"
    leech_db = r"D:\Projects\Content Delivery System (CDS)\ARYAN-TXT2LEECH__V2\cds_assets.db"

    if not os.path.exists(web_json):
        await status.edit_text("❌ <code>cds_assets.json</code> not found.")
        return

    with open(web_json, "r", encoding="utf-8") as f:
        assets = json.load(f)

    if not assets:
        await status.edit_text("ℹ️ No assets currently registered in the database.")
        return

    verified_count = 0
    purged_lectures = []

    msg_ids = [item.get("tgMessageId") for item in assets.values() if item.get("tgMessageId")]
    lecture_map = {item.get("tgMessageId"): lec_id for lec_id, item in assets.items() if item.get("tgMessageId")}

    for i in range(0, len(msg_ids), 50):
        chunk = msg_ids[i:i + 50]
        try:
            messages = await client.get_messages(chat_id=client.db_channel.id, message_ids=chunk)
            if not isinstance(messages, list):
                messages = [messages]

            for m in messages:
                if not m:
                    continue
                mid = m.id
                lec_id = lecture_map.get(mid)
                if m.empty or not (m.video or m.document):
                    purged_lectures.append((lec_id, mid))
                else:
                    verified_count += 1
        except Exception as e:
            print(f"[SYNC ERROR] {e}", flush=True)

    if purged_lectures:
        for lec_id, _ in purged_lectures:
            assets.pop(lec_id, None)

        with open(web_json, "w", encoding="utf-8") as f:
            json.dump(assets, f, indent=2)

        for db_path in [web_db, leech_db]:
            if os.path.exists(db_path):
                try:
                    conn = sqlite3.connect(db_path)
                    cur = conn.cursor()
                    for lec_id, _ in purged_lectures:
                        cur.execute("DELETE FROM video_assets WHERE lecture_id = ?", (lec_id,))
                    conn.commit()
                    conn.close()
                except Exception:
                    pass

    summary = (
        "📊 <b>Channel & Database Sync Complete!</b>\n\n"
        f"✅ <b>Active Verified Videos:</b> <code>{verified_count}</code>\n"
        f"🧹 <b>Purged Deleted/Missing:</b> <code>{len(purged_lectures)}</code>\n\n"
        "<i>Website and Channel are now 100% in sync!</i>"
    )
    await status.edit_text(summary, parse_mode=ParseMode.HTML)


@Bot.on_message(filters.command('enroll') & filters.private & filters.user(ADMINS))
async def enroll_user_cmd(client: Client, message: Message):
    """
    Usage: /enroll <telegram_id> <batch_id>
    """
    args = message.text.split()
    if len(args) < 3:
        await message.reply_text("<b>Usage:</b> <code>/enroll &lt;telegram_id&gt; &lt;batch_id&gt;</code>", parse_mode=ParseMode.HTML)
        return

    try:
        target_id = int(args[1])
    except ValueError:
        await message.reply_text("❌ Telegram ID must be a number.")
        return

    batch_id = args[2]
    await add_enrollment(target_id, batch_id, "active")
    await message.reply_text(f"✅ Successfully enrolled <code>{target_id}</code> into <code>{batch_id}</code>!", parse_mode=ParseMode.HTML)


@Bot.on_message(filters.command('unenroll') & filters.private & filters.user(ADMINS))
async def unenroll_user_cmd(client: Client, message: Message):
    """
    Usage: /unenroll <telegram_id> <batch_id>
    """
    args = message.text.split()
    if len(args) < 3:
        await message.reply_text("<b>Usage:</b> <code>/unenroll &lt;telegram_id&gt; &lt;batch_id&gt;</code>", parse_mode=ParseMode.HTML)
        return

    try:
        target_id = int(args[1])
    except ValueError:
        await message.reply_text("❌ Telegram ID must be a number.")
        return

    batch_id = args[2]
    await revoke_enrollment(target_id, batch_id)
    await message.reply_text(f"🚫 Revoked access for <code>{target_id}</code> in <code>{batch_id}</code>.", parse_mode=ParseMode.HTML)


@Bot.on_message(filters.command('myenrollments') & filters.private)
async def my_enrollments_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    enrollments = await get_user_enrollments(user_id)

    if not enrollments:
        await message.reply_text("You currently have no active course enrollments.")
        return

    text = "📚 <b>Your Enrolled Courses:</b>\n\n"
    for e in enrollments:
        status_emoji = "🟢" if e["status"] == "active" else "🔴"
        text += f"{status_emoji} <b>Batch:</b> <code>{e['batch_id']}</code> ({e['status']})\n"

    await message.reply_text(text, parse_mode=ParseMode.HTML)


@Bot.on_message(filters.private & ~filters.command(['start', 'testlink', 'enroll', 'unenroll', 'myenrollments']))
async def fallback_logger(client: Client, message: Message):
    sender_name = f"{message.from_user.first_name} {message.from_user.last_name or ''}".strip() if message.from_user else "Unknown"
    print(f"\n{'-'*60}", flush=True)
    print(f"[CDS BOT EVENT] [PRIVATE MSG RECEIVED]", flush=True)
    print(f"[CDS BOT EVENT] From: {sender_name} (@{message.from_user.username if message.from_user else ''}, ID: {message.from_user.id if message.from_user else 'None'})", flush=True)
    print(f"[CDS BOT EVENT] Text: '{message.text}'", flush=True)
    if message.media:
        print(f"[CDS BOT EVENT] Media Type: {message.media}", flush=True)
    print(f"{'-'*60}\n", flush=True)


@Bot.on_message(filters.command('start') & ~filters.private)
async def group_start_logger(client: Client, message: Message):
    sender_name = f"{message.from_user.first_name} {message.from_user.last_name or ''}".strip() if message.from_user else "Unknown"
    print(f"\n{'-'*60}", flush=True)
    print(f"[CDS BOT EVENT] [GROUP /start RECEIVED]", flush=True)
    print(f"[CDS BOT EVENT] In Chat: '{message.chat.title}' (ID: {message.chat.id})", flush=True)
    print(f"[CDS BOT EVENT] From: {sender_name} (@{message.from_user.username if message.from_user else ''}, ID: {message.from_user.id if message.from_user else 'None'})", flush=True)
    print(f"[CDS BOT EVENT] Text: '{message.text}'", flush=True)
    print(f"{'-'*60}\n", flush=True)

