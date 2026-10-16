#(©)Codexbotz

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from aiohttp import web
from plugins import web_server

import pyromod.listen
from pyrogram import Client
from pyrogram.enums import ParseMode
from datetime import datetime

from config import API_HASH, APP_ID, LOGGER, TG_BOT_TOKEN, TG_BOT_WORKERS, FORCE_SUB_CHANNEL, CHANNEL_ID, PORT
import pyrogram.utils

# Robust 64-bit Telegram Channel & Chat ID classification patch
def _patched_get_peer_type(peer_id: int) -> str:
    if peer_id < 0:
        if peer_id <= -1000000000000:
            return "channel"
        return "chat"
    elif peer_id > 0:
        return "user"
    raise ValueError(f"Peer id invalid: {peer_id}")

pyrogram.utils.get_peer_type = _patched_get_peer_type
pyrogram.utils.MIN_CHANNEL_ID = -1009999999999999
pyrogram.utils.MAX_CHANNEL_ID = -1000000000000


class Bot(Client):
    def __init__(self):
        if not TG_BOT_TOKEN or not API_HASH or not APP_ID:
            print(
                "\n[ERROR] Missing required Telegram credentials!\n"
                "Please configure TG_BOT_TOKEN, API_HASH, and APP_ID in your environment or .env file.\n"
                "Refer to .env.example for configuration details.\n",
                flush=True
            )
            sys.exit(1)

        super().__init__(
            name="Bot",
            api_hash=API_HASH,
            api_id=APP_ID,
            plugins={
                "root": "plugins"
            },
            workers=TG_BOT_WORKERS,
            bot_token=TG_BOT_TOKEN
        )
        self.LOGGER = LOGGER
        self.web_runner = None

    async def start(self):
        await super().start()
        usr_bot_me = await self.get_me()
        self.uptime = datetime.now()

        if FORCE_SUB_CHANNEL:
            try:
                link = (await self.get_chat(FORCE_SUB_CHANNEL)).invite_link
                if not link:
                    await self.export_chat_invite_link(FORCE_SUB_CHANNEL)
                    link = (await self.get_chat(FORCE_SUB_CHANNEL)).invite_link
                self.invitelink = link
            except Exception as a:
                self.LOGGER(__name__).warning(a)
                self.LOGGER(__name__).warning("Bot can't Export Invite link from Force Sub Channel!")
                self.LOGGER(__name__).warning(f"Please Double check the FORCE_SUB_CHANNEL value and Make sure Bot is Admin in channel with Invite Users via Link Permission, Current Force Sub Channel Value: {FORCE_SUB_CHANNEL}")
                self.LOGGER(__name__).info("\nBot Stopped. Join https://t.me/CodeXBotzSupport for support")
                sys.exit()
        try:
            print(f"[CDS BOT INIT] Resolving Storage Chat (CHANNEL_ID={CHANNEL_ID})...", flush=True)
            db_channel = await self.get_chat(CHANNEL_ID)
            self.db_channel = db_channel
            print(f"[CDS BOT INIT] Storage Chat Connected: Title='{db_channel.title}', ID={db_channel.id}, Type={db_channel.type}", flush=True)
            try:
                test = await self.send_message(chat_id = db_channel.id, text = "Test Message")
                await test.delete()
                print(f"[CDS BOT INIT] Storage Chat Write Permission Test: PASSED ✅", flush=True)
            except Exception as e_write:
                print(f"[CDS BOT INIT] Storage Chat Direct Write Notice: {e_write} (Read/Copy operations remain fully functional)", flush=True)
        except Exception as e:
            self.LOGGER(__name__).warning(e)
            print(f"[CDS BOT INIT] Storage Chat Resolution Warning: {type(e)} - {e}", flush=True)
            class FallbackChannel:
                id = CHANNEL_ID
                title = f"Channel {CHANNEL_ID}"
                type = "unknown"
            self.db_channel = FallbackChannel()

        self.set_parse_mode(ParseMode.HTML)
        self.username = usr_bot_me.username
        print(f"\n{'#'*60}", flush=True)
        print(f"[CDS BOT READY] Bot Username: @{usr_bot_me.username} (ID: {usr_bot_me.id})", flush=True)
        print(f"[CDS BOT READY] Storage Chat: '{getattr(self.db_channel, 'title', CHANNEL_ID)}' (ID: {getattr(self.db_channel, 'id', CHANNEL_ID)})", flush=True)
        print(f"[CDS BOT READY] Extreme Terminal Verbose Mode: ACTIVE 🚀", flush=True)
        print(f"[CDS BOT READY] Waiting for /start requests from web users...", flush=True)
        print(f"{'#'*60}\n", flush=True)
        
        # web-response: Bind primary PORT (default 8080)
        try:
            app = web.AppRunner(await web_server())
            await app.setup()
            bind_address = "0.0.0.0"
            await web.TCPSite(app, bind_address, PORT).start()
            self.web_runner = app
            print(f"[CDS BOT INIT] Web response server active on port {PORT}", flush=True)

            # Cloud resilience: If PORT is configured to a non-standard port (e.g. 8085),
            # also bind 8080 so default Koyeb/Render TCP/HTTP health checks pass immediately
            if PORT != 8080:
                try:
                    await web.TCPSite(app, bind_address, 8080).start()
                    print(f"[CDS BOT INIT] Auxiliary health check server active on port 8080", flush=True)
                except Exception as e_aux:
                    print(f"[CDS BOT INIT] Auxiliary port 8080 notice: {e_aux}", flush=True)
        except Exception as e_web:
            print(f"[CDS BOT INIT] Web response server notice: {e_web}", flush=True)

    async def stop(self, *args):
        if hasattr(self, "web_runner") and self.web_runner:
            try:
                await self.web_runner.cleanup()
            except Exception:
                pass
        await super().stop()
        self.LOGGER(__name__).info("Bot stopped.")
