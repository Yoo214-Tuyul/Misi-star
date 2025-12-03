import asyncio
import re
import aiohttp
import os
import urllib.parse
import random
from datetime import datetime
from pyrogram import Client
from pyrogram.types import InlineKeyboardMarkup

# ================ CONFIG (Final Clean) ================
VERBOSE = True                 # False to reduce console output
LOG_DIR = "logs"
JOIN_WAIT = 18                 # wait after join (seconds)
RETRY_WAIT = 20                # wait between verification retries (seconds)
RETRY_COUNT = 4                # number of verification retries
REFRESH_LIMIT = 50             # messages to scan when refreshing keyboard
MAX_CONCURRENT = 2             # max sessions running in parallel (lower -> safer)
CALLBACK_TIMEOUT = 40          # timeout for request_callback_answer
CALLBACK_RETRIES = 1           # ⬅⚡ hanya 1 kali attempt (permintaan kamu)
DELAY_AFTER_START_BOT = 8
JITTER_BEFORE_VERIFY_MIN = 1
JITTER_BEFORE_VERIFY_MAX = 5
SKIP_WAIT = 3

# Cooldown yang kamu minta: 20 detik setelah 1 putaran selesai
COOLDOWN_SECONDS = 10

os.makedirs(LOG_DIR, exist_ok=True)
SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT)


def now_ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log_write(session_name: str, text: str):
    ts = now_ts()
    line = f"{ts} | {session_name}: {text}"
    if VERBOSE:
        print(line)
    try:
        with open(os.path.join(LOG_DIR, f"{session_name}.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except:
        pass


def write_summary_file(success_list, fail_list):
    name = f"summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    path = os.path.join(LOG_DIR, name)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"Summary generated at {now_ts()}\n\n")
            f.write("SUCCESS:\n")
            for s in success_list:
                f.write(f"- {s}\n")
            f.write("\nFAILED:\n")
            for s in fail_list:
                f.write(f"- {s}\n")
        print(f"{now_ts()} | main: Summary saved -> {path}")
    except:
        pass


# ======================================================
# ==== Resolve redirect + join/start bot ===============
# ======================================================
async def resolve_and_join(url, app, session_name):
    resolved = url

    if "t.me/" not in url and "telegram.me" not in url and not url.startswith("tg:"):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, allow_redirects=True, timeout=10) as resp:
                    resolved = str(resp.url)
                    log_write(session_name, f"🔎 Redirect resolved -> {resolved}")
        except Exception as e:
            log_write(session_name, f"⚠ Gagal resolve redirect: {e}")

    try:
        parsed = urllib.parse.urlparse(resolved)
        path = (parsed.path or "").lstrip("/")
        query = urllib.parse.parse_qs(parsed.query)

        username_or_invite = path.split("/")[-1]

        # BOT
        if username_or_invite.lower().endswith("bot"):
            payload = query.get("start", [None])[0]
            try:
                if payload:
                    await app.send_message(username_or_invite, f"/start {payload}")
                else:
                    await app.send_message(username_or_invite, "/start")
                log_write(session_name, f"✔ Start bot @{username_or_invite}")
                await asyncio.sleep(DELAY_AFTER_START_BOT)
                return True
            except Exception as e:
                log_write(session_name, f"⚠ Tidak bisa start bot: {e}")
                return False

        # Invite
        inv = None
        if "+" in username_or_invite:
            inv = username_or_invite.split("+")[-1].split("?")[0]

        if inv:
            try:
                await app.import_chat_invite_link(f"https://t.me/+{inv}")
                log_write(session_name, "✔ Join via invite")
                return True
            except Exception as e:
                log_write(session_name, f"⚠ Gagal join via invite: {e}")

        # Join normal
        try:
            await app.join_chat(username_or_invite)
            log_write(session_name, f"✔ Join {username_or_invite}")
            return True
        except Exception:
            pass

        # Fallback
        try:
            await app.join_chat(resolved)
            log_write(session_name, f"✔ Join fallback {resolved}")
            return True
        except Exception as e2:
            log_write(session_name, f"⚠ Join gagal: {e2}")
            return False

    except Exception as e:
        log_write(session_name, f"⚠ Error parsing URL: {e}")
        return False


# ======================================================
# ==== Callback dengan retry minimal ===================
# ======================================================
async def safe_request_callback_answer(app, chat_id, message_id, callback_data, session_name):
    try:
        await asyncio.wait_for(
            app.request_callback_answer(chat_id=chat_id, message_id=message_id, callback_data=callback_data),
            timeout=CALLBACK_TIMEOUT
        )
        log_write(session_name, f"✔ Callback sukses")
        return True
    except asyncio.TimeoutError:
        log_write(session_name, f"⚠ Callback timeout (1 attempt saja)")
    except Exception as e:
        log_write(session_name, f"⚠ Callback error: {e}")

    return False


# ======================================================
# ==== Klik tombol join/verify =========================
# ======================================================
async def click_button(app, msg, session_name):
    if not msg or not msg.reply_markup:
        return False

    for row in msg.reply_markup.inline_keyboard:
        for btn in row:
            txt = (btn.text or "").lower()

            # JOIN
            if "перейти" in txt or "go" in txt or "🔍" in txt:
                if btn.url:
                    log_write(session_name, f"🔗 URL: {btn.url}")
                    joined = await resolve_and_join(btn.url, app, session_name)
                    await asyncio.sleep(JOIN_WAIT if joined else 2)

            # VERIFY
            if "подтверд" in txt or "verify" in txt or "✓" in txt:
                await asyncio.sleep(random.uniform(JITTER_BEFORE_VERIFY_MIN, JITTER_BEFORE_VERIFY_MAX))
                ok = await safe_request_callback_answer(
                    app, msg.chat.id, msg.id, btn.callback_data, session_name
                )
                return ok

    return False


# ======================================================
# ==== Skip Button =====================================
# ======================================================
async def click_skip_button(app, msg, session_name):
    if not msg or not msg.reply_markup:
        return False

    for row in msg.reply_markup.inline_keyboard:
        for btn in row:
            t = (btn.text or "").lower()
            if any(k in t for k in ("пропуст", "skip")):
                if btn.callback_data:
                    ok = await safe_request_callback_answer(
                        app, msg.chat.id, msg.id, btn.callback_data, session_name
                    )
                    if ok:
                        log_write(session_name, "✔ Skip berhasil")
                        await asyncio.sleep(SKIP_WAIT)
                        return True
                return False
    return False


# ======================================================
# ==== Proses 1 Misi ==================================
# ======================================================
async def process_mission(app, bot_username):
    session_name = app.name
    log_write(session_name, "▶ Ambil misi...")

    await app.send_message(bot_username, "/start")
    await asyncio.sleep(3)

    target = None
    async for msg in app.get_chat_history(bot_username, limit=REFRESH_LIMIT):
        if msg.reply_markup:
            target = msg
            break

    if not target:
        log_write(session_name, "❌ Tidak ada tombol misi")
        return False

    await asyncio.sleep(random.uniform(0.5, 2))

    await click_button(app, target, session_name)

    # cek sukses cepat
    async for msg in app.get_chat_history(bot_username, limit=REFRESH_LIMIT):
        if "задание выполнено" in (msg.text or "").lower():
            log_write(session_name, "🎉 Misi selesai!")
            return True

    # retry verify
    for attempt in range(RETRY_COUNT):
        log_write(session_name, f"🔁 Coba ulang {attempt+1}/{RETRY_COUNT}")
        await asyncio.sleep(random.uniform(0.5, 2))
        await click_button(app, target, session_name)
        await asyncio.sleep(RETRY_WAIT)

        async for msg in app.get_chat_history(bot_username, limit=REFRESH_LIMIT):
            if "задание выполнено" in (msg.text or "").lower():
                log_write(session_name, f"🎉 Selesai di retry {attempt+1}")
                return True

    # skip jika semua gagal
    await click_skip_button(app, target, session_name)
    return False


# ======================================================
# ==== Jalankan Satu Session ===========================
# ======================================================
async def run_session(session_name, bot_username, results):
    await SEMAPHORE.acquire()
    try:
        async with Client(session_name, workdir=".") as app:
            app._name = session_name
            log_write(session_name, "===== Mulai Session =====")
            ok = await process_mission(app, bot_username)
            results[session_name] = ok
    finally:
        SEMAPHORE.release()


# ======================================================
# ================= MAIN LOOP ==========================
# ======================================================
async def main():
    bot_username = input("Masukkan username bot (tanpa @): ")
    start_sess = int(input("Start session number: "))
    end_sess = int(input("End session number: "))

    sessions = [f"session_{i}" for i in range(start_sess, end_sess + 1)]

    while True:
        results = {}
        tasks = [run_session(s, bot_username, results) for s in sessions]
        await asyncio.gather(*tasks)

        success = [s for s, ok in results.items() if ok]
        fail = [s for s, ok in results.items() if not ok]

        log_write("main", f"BERHASIL: {success}")
        log_write("main", f"GAGAL  : {fail}")

        write_summary_file(success, fail)

        log_write("main", f"⏳ Cooldown {COOLDOWN_SECONDS}s sebelum ulang...")
        await asyncio.sleep(COOLDOWN_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
