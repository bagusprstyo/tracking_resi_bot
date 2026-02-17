import os
import sqlite3
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram import ReplyKeyboardMarkup, KeyboardButton

# --- KONFIGURASI ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_KEY = os.getenv("BINDERBYTE_API_KEY")
# ------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Membuat tombol menu statis (Reply Keyboard)
    keyboard = [
        [KeyboardButton("📋 Daftar Paket Aktif")],
        [KeyboardButton("❓ Cara Cek")]
    ]
    # resize_keyboard=True agar ukuran tombol pas (tidak terlalu raksasa)
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    await update.message.reply_text(
        "Halo! 👋 Selamat datang di *Bot Cek Resi*.\n\n"
        "Gunakan menu di bawah untuk akses cepat atau langsung kirim nomor resi kamu.",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer() # Menghilangkan loading pada tombol

    if query.data == 'jalankan_list':
        # Memanggil fungsi list_resi yang sudah ada
        await list_resi(query, context)
    elif query.data == 'tampil_bantuan':
        await query.message.reply_text(
            "📝 *Cara Cek Resi:*\n\n"
            "1. *Otomatis*: Langsung kirim nomor resi.\n"
            "2. *Manual*: `cek <kurir> <resi>`\n"
            "3. *Dengan Nama*: `cek <kurir> <resi> <nama barang>`\n\n"
            "Contoh: `cek spx SPX123 Sepatu Baru`",
            parse_mode="Markdown"
        )

def init_db():
    with sqlite3.connect('resi_database.db') as conn:
        cursor = conn.cursor()

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS resi_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            courier TEXT,
            resi TEXT UNIQUE,
            alias TEXT
        )
        """)

    conn.commit()
    conn.close()


def simpan_resi(courier, resi, alias="Paket Tanpa Nama"):

    init_db()
    with sqlite3.connect('resi_database.db') as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT OR REPLACE INTO resi_data (courier, resi, alias)
                VALUES (?, ?, ?)
                """,
                (courier.lower(), resi.upper(), alias)
            )
            conn.commit()
        except Exception as e:
            print(f"Error simpan db: {e}")


async def list_resi(update_or_query, context: ContextTypes.DEFAULT_TYPE):
    is_query = hasattr(update_or_query, 'message')
    target = update_or_query.message if is_query else update_or_query.message

    # Ambil data dari Database
    init_db()
    with sqlite3.connect('resi_database.db') as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT courier, resi, alias FROM resi_data')
        rows = cursor.fetchall()
    

    if not rows:
        await target.reply_text("📭 *Belum ada resi tersimpan di database.*", parse_mode="Markdown")
        return

    status_msg = await target.reply_text("🔄 *Memperbarui status paket dari database...*", parse_mode="Markdown")
    message = "📋 *DAFTAR PAKET AKTIF*\n━━━━━━━━━━━━━━━━━━\n\n"
    resi_tetap_simpan = []

    for courier, resi, alias in rows:
        try:
            url = "https://api.binderbyte.com/v1/track"
            params = {"api_key": API_KEY, "courier": courier, "awb": resi}
            data = requests.get(url, params=params).json()

            if data.get("status") == 200:
                summary = data["data"].get("summary", {})
                history = data["data"].get("history", [])
                status_raw = summary.get("status", "-").upper()
                last_pos = history[0].get("desc", "-") if history else "Data belum tersedia"

                if status_raw == "DELIVERED":
                    with sqlite3.connect('resi_database.db') as conn_del:
                        cursor_del = conn_del.cursor()
                        cursor_del.execute('DELETE FROM resi_data WHERE resi = ?', (resi,))
                        conn_del.commit()

                    message += f"✅ *{alias}*\n└ `{resi}` (Selesai & Dihapus dari DB)\n\n"
                else:
                    message += (
                        f" 📦 *{alias}*\n"
                        f" 🚚 `{courier.upper()}` - `{resi}`\n"
                        f" 📌 *{status_raw}*\n"
                        f" 📍 {last_pos}\n\n"
                    )
            else:
                message += f"📦 *{alias}*\n└ `{resi}` (Gagal Update)\n\n"

        except Exception as e:
            print("Error list_resi:", e)
            continue


    await status_msg.delete() 
    await target.reply_text(message, parse_mode="Markdown")
async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_text = update.message.text.strip()
    
    # LOGIKA BARU: Jika user klik tombol di bawah bar pesan
    if raw_text == "📋 Daftar Paket Aktif":
        await list_resi(update, context)
        return
    elif raw_text == "❓ Cara Cek":
        await update.message.reply_text(
            "📝 *Format Cek Resi:*\n\n"
            "• Kirim resi saja: `SPX123` (Auto-detect)\n"
            "• Resi + Nama: `SPX123 Sepatu` (Auto + Nama)\n"
            "• Manual: `cek kurir resi nama`",
            parse_mode="Markdown"
        )
        return
    else:
        # JIKA USER LANGSUNG KIRIM RESI: <resi> <alias> (AUTO DETECT)
        parts_auto = raw_text.split(" ", 1)
        resi = parts_auto[0].upper()
        alias = parts_auto[1] if len(parts_auto) > 1 else "Paket Tanpa Nama"

        
        courier = detect_courier(resi)
        if not courier:
            await update.message.reply_text("🔍 *Kurir tidak terdeteksi otomatis.*\nGunakan format: `cek <kurir> <resi>`", parse_mode="Markdown")
            return

    # PROSES KE API
    url = "https://api.binderbyte.com/v1/track"
    params = {"api_key": API_KEY, "courier": courier, "awb": resi}

    try:
        response = requests.get(url, params=params, timeout=10)

        print("STATUS CODE:", response.status_code)
        print("RAW RESPONSE:", response.text)

        data = response.json()

        if response.status_code != 200:
            await update.message.reply_text(f"❌ HTTP Error: {response.status_code}")
            return

        if data.get("status") != 200:
            await update.message.reply_text(
                f"❌ API Error: {data.get('message', 'Resi tidak ditemukan')}",
                parse_mode="Markdown"
            )
            return

        s = data.get("data", {}).get("summary", {})
        h = data.get("data", {}).get("history", [])

        msg = (
            f"✅ *DATA DITEMUKAN*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📦 *Barang* : {alias}\n"
            f"🚚 *Ekspedisi* : {s.get('courier', '').upper()}\n"
            f"🆔 *No Resi* : `{resi}`\n"
            f"📌 *Status* : `{s.get('status', '-').upper()}`\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"🕒 *RIWAYAT TERAKHIR*\n"
        )

        for item in h[:2]:
            msg += f"• _{item.get('date', '-')}_\n  └ {item.get('desc', '-')}\n\n"

        await update.message.reply_text(msg)

        if s.get('status', '').upper() != "DELIVERED":
            simpan_resi(courier, resi, alias)

    except Exception as e:
        print("ERROR DETAIL:", e)
        await update.message.reply_text(f"❌ Error detail: {e}")


def detect_courier(resi):
    resi = resi.upper()
    if resi.startswith("SPX"): return "spx"
    elif resi.startswith(("JD", "JP")): return "jnt"
    elif resi.startswith("JZ"): return "jnt_cargo"
    elif resi.isdigit() and len(resi) >= 12: return "sicepat"
    elif (len(resi) == 10 or len(resi) == 12) and resi.isdigit(): return "jne"
    return None

if __name__ == '__main__':
    # 1. Inisialisasi Database SQLite
    init_db()
    
    print("🚀 Bot nyala dengan Database SQLite & Fitur Tombol...")
    
    # 2. Build Application
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # 3. Registrasi Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("list", list_resi))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    # 4. Jalankan Bot

    app.run_polling()

