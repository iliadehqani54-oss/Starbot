import re
import sqlite3
import math
import re
import uuid
import httpx
from datetime import datetime, time, timedelta, timedelta
from zoneinfo import ZoneInfo
from night_messages import get_night_message

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from dotenv import load_dotenv
import os
load_dotenv()
TOKEN = os.getenv("TOKEN")
print("TOKEN CHECK:", bool(TOKEN), len(TOKEN or ""))

ADMIN_ID = 7959551548

STAR_PRICE = 0.02

CARD_1 = "5047061066566791"
CARD_2 = "6219861478620270"

NOBITEX_URL = "https://apiv2.nobitex.ir/v3/orderbook/USDTIRT"

TON_ADDRESS = "UQAiL4XVfjZiiZnUI6aPLRuT7q40DvJb3p_dJCkHYRFTJYDA"

FRAGMENT_URL = "https://fragment.com/stars/buy?recipient=GNp9vyOZvZQbdwhL61uieioXWchhY3cSTJ0xcsFod_zbRdE7ateWU5Zjw3r-QfLT&quantity=50"

# مقدار اولیه؛ بعد از اولین بروزرسانی از Fragment خوانده می‌شود
TON_PER_50_STARS = 0.5448
TON_PER_STAR = TON_PER_50_STARS / 50

async def get_fragment_50_stars_price():
    try:
        async with httpx.AsyncClient(
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0"}
        ) as client:
            response = await client.get(FRAGMENT_URL)
            response.raise_for_status()

        html = response.text

        pattern = (
            r'<input type="radio" class="radio" name="stars" value="50" checked>'
            r'.*?'
            r'<div class="tm-radio-label">.*?50 Stars.*?</div>'
            r'.*?'
            r'<div class="tm-value icon-before icon-usd">([0-9.]+)</div>'
            r'.*?'
            r'<div class="tm-radio-desc wide-only icon-before icon-ton">'
            r'0<span class="mini-frac">\.([0-9]+)</span>'
        )

        match = re.search(pattern, html, re.S)

        if not match:
            print("Fragment price not found")
            return None

        usd_price = float(match.group(1))
        ton_fraction = match.group(2)

        ton_price = float("0." + ton_fraction)

        print(f"Fragment 50 Stars: ${usd_price:.2f}")
        print(f"Fragment 50 Stars: {ton_price:.4f} TON")

        return ton_price

    except Exception as e:
        print("Fragment price error:", e)
        return None


async def update_fragment_price():
    global TON_PER_50_STARS, TON_PER_STAR

    ton_price = await get_fragment_50_stars_price()

    if ton_price is None:
        return False

    TON_PER_50_STARS = ton_price
    TON_PER_STAR = ton_price / 50

    print(
        f"UPDATED: 50 Stars = {TON_PER_50_STARS:.4f} TON | "
        f"1 Star = {TON_PER_STAR:.8f} TON"
    )

    await update_prices()

    return True




async def get_real_ton_balance():
    url = f"https://tonapi.io/v2/accounts/{TON_ADDRESS}"

    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(
                    url,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                response.raise_for_status()

                data = response.json()
                balance = int(data["balance"]) / 1_000_000_000

                print(f"REAL TON BALANCE: {balance:.9f}")
                return balance

        except Exception as e:
            print(
                f"TON balance attempt {attempt + 1}/3 failed: "
                f"{type(e).__name__}: {e}"
            )

    print("TON balance error: all attempts failed")
    return None


async def get_real_stars_capacity():
    ton_balance = await get_real_ton_balance()

    if ton_balance is None:
        return None

    if TON_PER_STAR <= 0:
        return None

    real_capacity = math.floor(ton_balance / TON_PER_STAR)

    # فقط اگر رقم سوم 5 یا بیشتر باشد،
    # به صدتایی بعدی گرد می‌شود.
    # مثال:
    # 2301 -> 2301
    # 2349 -> 2349
    # 2350 -> 2400
    # 2450 -> 2500
    if real_capacity % 100 >= 50:
        display_capacity = ((real_capacity // 100) + 1) * 100
    else:
        display_capacity = real_capacity

    return ton_balance, display_capacity


async def sync_safebox_with_wallet(context=None):
    result = await get_real_stars_capacity()

    if result is None:
        print("❌ Safebox sync skipped: wallet capacity unavailable")
        return False

    ton_balance, capacity = result

    conn = sqlite3.connect(DB)

    try:
        row = conn.execute(
            "SELECT total, reserved FROM safebox WHERE id=1"
        ).fetchone()

        if not row:
            print("❌ Safebox row not found")
            return False

        old_total, reserved = row

        # total از ظرفیت واقعی کیف پول می‌آید.
        # reserved دست‌نخورده باقی می‌ماند.
        new_total = capacity

        conn.execute(
            "UPDATE safebox SET total=? WHERE id=1",
            (new_total,),
        )

        conn.commit()

        available = max(0, new_total - reserved)

        print(
            f"🔄 SAFEBOX SYNC | "
            f"TON={ton_balance:.9f} | "
            f"TOTAL={new_total} | "
            f"RESERVED={reserved} | "
            f"AVAILABLE={available}"
        )

        return True

    except Exception as e:
        conn.rollback()
        print(f"❌ Safebox sync error: {type(e).__name__}: {e}")
        return False

    finally:
        conn.close()


# ---------------- TELEGRAM GIFTS ----------------

TELEGRAM_GIFTS = [
    ("گیفت قلب 💝", 0.21),
    ("گیفت عروسک تدی 🐻", 0.21),
    ("گیفت جعبه کادو 🎁", 0.356),
    ("گیفت گل رز 🌹", 0.365),
    ("گیفت کیک تولد 🎂", 0.71),
    ("گیفت سفینه فضایی 🚀", 0.71),
    ("گیفت بطری نوشیدنی 🍾", 0.71),
    ("گیفت جام 🏆", 1.40),
    ("گیفت حلقه ازدواج 💍", 1.40),
    ("گیفت الماس 💎", 1.40),
]

DB = "orders.db"
SAFEBOX_START = 2000


# ---------------- DATABASE ----------------

def get_safebox():
    conn = sqlite3.connect(DB)
    try:
        row = conn.execute(
            "SELECT total, reserved FROM safebox WHERE id=1"
        ).fetchone()
        return row
    finally:
        conn.close()


def reserve_stars(amount):
    conn = sqlite3.connect(DB)
    try:
        row = conn.execute(
            "SELECT total, reserved FROM safebox WHERE id=1"
        ).fetchone()

        if not row:
            return False

        total, reserved = row
        available = total - reserved

        if amount > available:
            return False

        conn.execute(
            "UPDATE safebox SET reserved = reserved + ? WHERE id=1",
            (amount,)
        )
        conn.commit()
        return True

    finally:
        conn.close()


def init_db():
    conn = sqlite3.connect(DB)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE,
            user_id INTEGER,
            username TEXT,
            stars INTEGER,
            usdt REAL,
            total INTEGER,
            card TEXT,
            status TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    try:
        conn.execute(
            "ALTER TABLE orders ADD COLUMN recipient_username TEXT"
        )
    except sqlite3.OperationalError:
        pass


    try:
        conn.execute(
            "ALTER TABLE orders ADD COLUMN payment_expires_at TEXT"
        )
    except sqlite3.OperationalError:
        pass

    # Gift order fields
    try:
        conn.execute(
            "ALTER TABLE orders ADD COLUMN product_type TEXT DEFAULT 'stars'"
        )
    except sqlite3.OperationalError:
        pass

    try:
        conn.execute(
            "ALTER TABLE orders ADD COLUMN product_name TEXT"
        )
    except sqlite3.OperationalError:
        pass

    try:
        conn.execute(
            "ALTER TABLE orders ADD COLUMN product_ton REAL"
        )
    except sqlite3.OperationalError:
        pass

    # ---------------- NIGHTLY MESSAGE TABLES ----------------

    conn.execute("""
        CREATE TABLE IF NOT EXISTS bot_users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            night_messages INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS night_message_state (
            id INTEGER PRIMARY KEY,
            message_index INTEGER NOT NULL DEFAULT 0
        )
    """)

    conn.execute(
        "INSERT OR IGNORE INTO night_message_state "
        "(id, message_index) VALUES (1, 0)"
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS safebox (
            id INTEGER PRIMARY KEY,
            total INTEGER NOT NULL,
            reserved INTEGER NOT NULL DEFAULT 0
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_usdt_price (
            id INTEGER PRIMARY KEY,
            price REAL NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    conn.execute(
        "INSERT OR IGNORE INTO safebox (id, total, reserved) "
        "VALUES (1, ?, 0)",
        (SAFEBOX_START,),
    )

    conn.commit()
    conn.close()


def create_order(
    user_id,
    username,
    stars,
    usdt,
    total,
    recipient_username,
):
    code = "ST-" + uuid.uuid4().hex[:8].upper()

    conn = sqlite3.connect(DB)

    conn.execute(
        """
        INSERT INTO orders
        (
            code,
            user_id,
            username,
            stars,
            usdt,
            total,
            card,
            status,
            recipient_username
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            code,
            user_id,
            username,
            stars,
            usdt,
            total,
            "",
            "awaiting_payment",
            recipient_username,
        ),
    )

    conn.commit()
    conn.close()

    return code


def update_card(code, card):
    conn = sqlite3.connect(DB)

    conn.execute(
        "UPDATE orders SET card=? WHERE code=?",
        (card, code),
    )

    conn.commit()
    conn.close()


def get_order(code):
    conn = sqlite3.connect(DB)

    row = conn.execute(
        """
        SELECT code, user_id, username, stars,
               usdt, total, card, status, recipient_username,
               product_type, product_name, product_ton
        FROM orders
        WHERE code=?
        """,
        (code,),
    ).fetchone()

    conn.close()

    return row


    
def expire_order_reservation(code):
    conn = sqlite3.connect(DB)
    try:
        row = conn.execute(
            "SELECT stars, status FROM orders WHERE code=?",
            (code,),
        ).fetchone()

        if not row:
            return False

        stars, status = row

        if status != "awaiting_payment":
            return False

        conn.execute(
            """
            UPDATE safebox
            SET reserved = MAX(0, reserved - ?)
            WHERE id=1
            """,
            (stars,),
        )

        conn.execute(
            "UPDATE orders SET status='expired' WHERE code=?",
            (code,),
        )

        conn.commit()
        return True

    finally:
        conn.close()


def release_order_reservation(code):
    conn = sqlite3.connect(DB)
    try:
        row = conn.execute(
            "SELECT stars, status FROM orders WHERE code=?",
            (code,),
        ).fetchone()

        if not row:
            return False

        stars, status = row

        if status not in ("awaiting_payment", "awaiting_review"):
            return False

        conn.execute(
            """
            UPDATE safebox
            SET reserved = MAX(0, reserved - ?)
            WHERE id=1
            """,
            (stars,),
        )

        conn.commit()
        return True

    finally:
        conn.close()


async def expire_payment_orders(context):
    conn = sqlite3.connect(DB)

    rows = conn.execute(
        """
        SELECT code
        FROM orders
        WHERE status='awaiting_payment'
          AND payment_expires_at IS NOT NULL
          AND datetime(payment_expires_at) <= datetime('now')
        """
    ).fetchall()

    conn.close()

    for (code,) in rows:
        if expire_order_reservation(code):
            print(f"⏰ Order expired: {code}")

def update_status(code, status):
    conn = sqlite3.connect(DB)

    conn.execute(
        "UPDATE orders SET status=? WHERE code=?",
        (status, code),
    )

    conn.commit()
    conn.close()


# ---------------- PRICE SOURCES ----------------

TON_URL = "https://tonapi.io/v2/rates?tokens=ton&currencies=USD"

USDT_MARKUP = 2000


async def get_ton_price():
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(TON_URL)
            response.raise_for_status()

            data = response.json()
            return float(data["rates"]["TON"]["prices"]["USD"])

    except Exception as e:
        print("TON price error:", e)
        return None


async def get_usdt_from_nobitex():
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(NOBITEX_URL)
            response.raise_for_status()

            data = response.json()

            if data.get("status") != "ok":
                return None

            price_rial = float(data["lastTradePrice"])
            return price_rial / 10

    except Exception as e:
        print("Nobitex error:", e)
        return None


async def update_prices():
    import asyncio

    # حداکثر 5 دقیقه برای پیدا کردن هر دو قیمت تلاش می‌کنیم
    for attempt in range(10):
        ton_price = await get_ton_price()
        usdt_price = await get_usdt_from_nobitex()

        if ton_price is not None and usdt_price is not None:
            usdt_price += USDT_MARKUP

            # قیمت خرید 50 Stars طبق فرمول Fragment
            price_50_stars = TON_PER_50_STARS * ton_price * usdt_price

            # قیمت خرید هر Star
            buy_star_price = price_50_stars / 50

            # 18 درصد سود
            star_price = buy_star_price * 1.18

            conn = sqlite3.connect(DB)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS current_prices (
                    id INTEGER PRIMARY KEY,
                    ton_usd REAL NOT NULL,
                    usdt_toman REAL NOT NULL,
                    star_toman REAL NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            conn.execute("""
                INSERT INTO current_prices
                    (id, ton_usd, usdt_toman, star_toman, updated_at)
                VALUES (1, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    ton_usd=excluded.ton_usd,
                    usdt_toman=excluded.usdt_toman,
                    star_toman=excluded.star_toman,
                    updated_at=excluded.updated_at
            """, (
                ton_price,
                usdt_price,
                star_price,
                datetime.now().isoformat(),
            ))

            conn.commit()
            conn.close()

            print(
                f"Prices updated | "
                f"TON=${ton_price:.4f} | "
                f"USDT={usdt_price:,.0f} | "
                f"STAR={star_price:,.0f}"
            )

            return star_price

        print(
            f"Price update failed "
            f"(attempt {attempt + 1}/10)"
        )

        if attempt < 9:
            await asyncio.sleep(30)

    print("Price update failed after 5 minutes")
    return None


def get_current_star_price():
    conn = sqlite3.connect(DB)
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS current_prices (
            id INTEGER PRIMARY KEY,
            ton_usd REAL NOT NULL,
            usdt_toman REAL NOT NULL,
            star_toman REAL NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    row = conn.execute(
        "SELECT star_toman FROM current_prices WHERE id=1"
    ).fetchone()

    conn.close()

    if row is None:
        return None

    return row[0]


# ---------------- GIFT PRICING ----------------

def get_current_ton_usdt():
    conn = sqlite3.connect(DB)

    row = conn.execute(
        """
        SELECT ton_usd, usdt_toman
        FROM current_prices
        WHERE id=1
        """
    ).fetchone()

    conn.close()

    if row is None:
        return None

    return row[0], row[1]


def get_gift_price_toman(gift_ton):
    prices = get_current_ton_usdt()

    if prices is None:
        return None

    ton_usd, usdt_toman = prices

    return round(gift_ton * ton_usd * usdt_toman)



# ---------------- START ----------------



async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    conn = sqlite3.connect(DB)
    conn.execute(
        """
        INSERT INTO bot_users (user_id, username)
        VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET username=excluded.username
        """,
        (
            user.id,
            user.username or "",
        ),
    )
    conn.commit()
    conn.close()

    keyboard = [
        [
            InlineKeyboardButton(
                "⭐ خرید Stars",
                callback_data="buy",
                style="success",
            )
        ],

        [
            InlineKeyboardButton(
                "🎁 گیفت تلگرام 💝",
                callback_data="gifts",
                style="primary",
            )
        ],

        [
            InlineKeyboardButton(
                "🆘 پشتیبانی",
                callback_data="support",
                style="danger",
            )
        ],
    ]

    await update.message.reply_text(
        "به ربات فروش استارز خوش آمدید⭐️\n\n"
        "ما اینجا سعی میکنیم سفارش شما را به قیمت مناسب و بروز با پرداخت ریالی و در کمترین تایم ممکن انجام دهیم✅️❤️\n\n"
        "یکی از گزینه‌های زیر را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ---------------- BUTTONS ----------------

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    print(
        f"CALLBACK: data={query.data!r} "
        f"user_id={query.from_user.id} "
        f"admin_id={ADMIN_ID}"
    )

    await query.answer()

    # BACK TO MAIN MENU
    if query.data == "back":
        context.user_data["waiting_recipient_username"] = False
        context.user_data["waiting_custom_stars"] = False
        context.user_data["waiting_receipt"] = False
        context.user_data["current_order"] = None

        for key in (
            "pending_stars",
            "pending_usdt",
            "pending_total",
            "pending_username",
            "pending_user_id",
        ):
            context.user_data.pop(key, None)

        keyboard = [
            [
                InlineKeyboardButton(
                    "⭐ خرید Stars",
                        style="success",
                    callback_data="buy",
                )
            ],

            [
                InlineKeyboardButton(
                    "🆘 پشتیبانی",
                        style="danger",
                    callback_data="support",
                )
            ],
        ]

        await query.edit_message_text(
            "🏠 منوی اصلی",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

        return

    # NIGHTLY MESSAGE - OPEN STORE

    if query.data == "nightly_store":

        keyboard = [
            [
                InlineKeyboardButton(
                    "⭐ خرید Stars",
                        style="success",
                    callback_data="buy",
                )
            ],
            [
                InlineKeyboardButton(
                    "🎁 گیفت تلگرام 💝",
                        style="primary",
                    callback_data="gifts",
                )
            ],
            [
                InlineKeyboardButton(
                    "🆘 پشتیبانی",
                        style="danger",
                    callback_data="support",
                )
            ],
        ]

        await query.edit_message_text(
            "به ربات فروش استارز خوش آمدید⭐️\n\n"
            "ما اینجا سعی میکنیم سفارش شما را به قیمت مناسب و بروز "
            "با پرداخت ریالی و در کمترین تایم ممکن انجام دهیم✅️❤️\n\n"
            "یکی از گزینه‌های زیر را انتخاب کنید:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

        return


    # NIGHTLY MESSAGE - DISABLE

    if query.data == "disable_nightly":

        user_id = query.from_user.id

        conn = sqlite3.connect(DB)
        conn.execute(
            """
            UPDATE bot_users
            SET night_messages=0
            WHERE user_id=?
            """,
            (user_id,),
        )
        conn.commit()
        conn.close()

        keyboard = [
            [
                InlineKeyboardButton(
                    "🛍️ فروشگاه محصولات",
                    callback_data="nightly_store",
                )
            ]
        ]

        await query.edit_message_text(
            "🔕 پیام‌های شبانه برای شما غیرفعال شد.\n\n"
            "هر زمان خواستی دوباره از طریق فروشگاه وارد ربات شو ❤️",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

        return


    # GIFTS

    if query.data == "gifts":

        keyboard = []

        for index, (gift_name, gift_ton) in enumerate(TELEGRAM_GIFTS):
            price_toman = get_gift_price_toman(gift_ton)

            if price_toman is None:
                price_text = "قیمت نامشخص"
            else:
                price_text = f"{price_toman:,} تومان"

            keyboard.append([
                InlineKeyboardButton(
                    f"{gift_name} | {price_text}",
                    callback_data=f"gift_{index}",
                )
            ])

        keyboard.append([
            InlineKeyboardButton(
                "🔙 بازگشت",
                callback_data="back",
            )
        ])

        await query.edit_message_text(
            "🎁 گیفت‌های تلگرام 💝\n\n"
            "محصول موردنظر خود را انتخاب کنید:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

        return


    # SELECT GIFT

    if query.data.startswith("gift_"):

        try:
            gift_index = int(query.data.split("_")[1])
            gift_name, gift_ton = TELEGRAM_GIFTS[gift_index]
        except (ValueError, IndexError):
            await query.edit_message_text(
                "❌ گیفت انتخاب‌شده معتبر نیست."
            )
            return

        price_toman = get_gift_price_toman(gift_ton)

        if price_toman is None:
            await query.edit_message_text(
                "❌ قیمت گیفت در حال حاضر در دسترس نیست.\n"
                "لطفاً چند لحظه بعد دوباره تلاش کنید."
            )
            return

        context.user_data["waiting_gift_recipient"] = True
        context.user_data["pending_gift_name"] = gift_name
        context.user_data["pending_gift_ton"] = gift_ton
        context.user_data["pending_total"] = price_toman
        context.user_data["pending_username"] = (
            query.from_user.username or ""
        )
        context.user_data["pending_user_id"] = query.from_user.id

        keyboard = [
            [
                InlineKeyboardButton(
                    "🔙 بازگشت",
                    callback_data="gifts",
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ لغو سفارش",
                    callback_data="cancel_order",
                )
            ],
        ]

        await query.edit_message_text(
            f"🎁 محصول انتخابی: {gift_name}\n\n"
            f"💎 قیمت: {price_toman:,} تومان\n"
            f"💠 قیمت پایه: {gift_ton} TON\n\n"
            "👤 آیدی تلگرام گیرنده را وارد کنید.\n\n"
            "مثال:\n"
            "@username",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

        return


    # BUY

    if query.data == "buy":

        keyboard = [
            [
                InlineKeyboardButton(
                    "⭐ 50",
                    callback_data="stars_50",
                ),
                InlineKeyboardButton(
                    "⭐ 100",
                    callback_data="stars_100",
                ),
            ],
            [
                InlineKeyboardButton(
                    "⭐ 500",
                    callback_data="stars_500",
                ),
                InlineKeyboardButton(
                    "⭐ 1000",
                    callback_data="stars_1000",
                ),
            ],
            [
                InlineKeyboardButton(
                    "⭐ 2000",
                    callback_data="stars_2000",
                ),
            ],
            [
                InlineKeyboardButton(
                    "✏️ تعداد دلخواه Stars",
                    callback_data="custom_stars",
                )
            ],
        ]

        await query.edit_message_text(
            "⭐ تعداد Stars را انتخاب کنید:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

        return
# WALLET
    if query.data == "custom_stars":

        context.user_data["waiting_custom_stars"] = True

        keyboard = [
            [
                InlineKeyboardButton(
                    "🔙 بازگشت",
                    callback_data="buy",
                )
            ]
        ]

        await query.edit_message_text(
            "✏️ تعداد دلخواه Stars را وارد کنید.\n\n"
            "⭐ حداقل: 50 Stars\n"
            "⭐ حداکثر: 2000 Stars\n\n"
            "مثلاً: 750",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

        return

    # ORDERS

    if query.data == "orders":

        conn = sqlite3.connect(DB)

        rows = conn.execute(
            """
            SELECT code, stars, total, status
            FROM orders
            WHERE user_id=?
            ORDER BY id DESC
            LIMIT 10
            """,
            (query.from_user.id,),
        ).fetchall()

        conn.close()

        if not rows:
            await query.edit_message_text(
                "📦 هنوز سفارشی ندارید."
            )
            return

        text = "📦 سفارش‌های شما:\n\n"

        for code, stars, total, status in rows:
            text += (
                f"🧾 {code}\n"
                f"⭐ {stars:,} Stars\n"
                f"💰 {total:,} تومان\n"
                f"📌 {status}\n\n"
            )

        await query.edit_message_text(text)

        return

    # SUPPORT

    if query.data == "support":

        keyboard = [
            [
                InlineKeyboardButton(
                    "💬 تماس با پشتیبانی",
                    url="https://t.me/ILICHIN0",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔙 بازگشت",
                    callback_data="back",
                )
            ],
        ]

        await query.edit_message_text(
            "🆘 پشتیبانی\n\n"
            "برای ارتباط با پشتیبانی روی دکمه زیر بزنید.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

        return

    # STARS

    if query.data.startswith("stars_"):

        stars = int(query.data.split("_")[1])

        safe = get_safebox()

        if safe is None:
            await query.edit_message_text(
                "❌ خطا در بررسی موجودی گاوصندوق."
            )
            return

        safe_total, safe_reserved = safe
        available = safe_total - safe_reserved

        if stars > available:
            await query.edit_message_text(
                f"❌ موجودی کافی نیست.\\n\\n"
                f"⭐ موجودی قابل فروش: {available:,} Stars\\n"
                f"⭐ درخواست شما: {stars:,} Stars"
            )
            return

        star_price = get_current_star_price()

        if star_price is None:
            await query.edit_message_text(
                "❌ قیمت Stars هنوز بروزرسانی نشده است.\n"
                "لطفاً چند لحظه بعد دوباره تلاش کنید."
            )
            return

        total = round(stars * star_price)

        username = query.from_user.username or ""

        context.user_data["waiting_recipient_username"] = True
        context.user_data["pending_stars"] = stars
        context.user_data["pending_total"] = total
        context.user_data["pending_username"] = username
        context.user_data["pending_user_id"] = query.from_user.id

        keyboard = [
            [
                InlineKeyboardButton(
                    "🔙 بازگشت",
                    callback_data="recipient_back",
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ لغو سفارش",
                    callback_data="cancel_order",
                )
            ]
        ]

        await query.edit_message_text(
            "👤 آیدی تلگرام گیرنده Stars را وارد کنید.\n\n"
            "مثال:\n"
            "@username\n\n"
            "⚠️ آیدی باید با @ شروع شود.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

        return


  
    # CANCEL ORDER / RETURN TO MAIN MENU
    if query.data == "cancel_order":
        context.user_data["waiting_recipient_username"] = False
        context.user_data["waiting_custom_stars"] = False
        context.user_data["waiting_gift_recipient"] = False
        context.user_data["waiting_receipt"] = False
        context.user_data["current_order"] = None

        for key in (
            "pending_stars",
            "pending_usdt",
            "pending_total",
            "pending_username",
            "pending_user_id",
            "pending_gift_name",
            "pending_gift_ton",
        ):
            context.user_data.pop(key, None)

        keyboard = [
            [
                InlineKeyboardButton(
                    "⭐ خرید Stars",
                        style="success",
                    callback_data="buy",
                )
            ],
            [
                InlineKeyboardButton(
                    "🎁 گیفت تلگرام 💝",
                        style="primary",
                    callback_data="gifts",
                )
            ],
            [
                InlineKeyboardButton(
                    "🆘 پشتیبانی",
                        style="danger",
                    callback_data="support",
                )
            ],
        ]

        await query.edit_message_text(
            "به ربات فروش استارز خوش آمدید⭐️\n\n"
            "ما اینجا سعی میکنیم سفارش شما را به قیمت مناسب و بروز "
            "با پرداخت ریالی و در کمترین تایم ممکن انجام دهیم✅️❤️\n\n"
            "یکی از گزینه‌های زیر را انتخاب کنید:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return


    # RECIPIENT BACK
    if query.data == "recipient_back":
        context.user_data["waiting_recipient_username"] = False

        for key in (
            "pending_stars",
            "pending_usdt",
            "pending_total",
            "pending_username",
            "pending_user_id",
        ):
            context.user_data.pop(key, None)

        keyboard = [
            [
                InlineKeyboardButton(
                    "⭐ 50",
                    callback_data="stars_50",
                ),
                InlineKeyboardButton(
                    "⭐ 100",
                    callback_data="stars_100",
                ),
            ],
            [
                InlineKeyboardButton(
                    "⭐ 500",
                    callback_data="stars_500",
                ),
                InlineKeyboardButton(
                    "⭐ 1000",
                    callback_data="stars_1000",
                ),
            ],
            [
                InlineKeyboardButton(
                    "⭐ 2000",
                    callback_data="stars_2000",
                ),
            ],
            [
                InlineKeyboardButton(
                    "✏️ تعداد دلخواه Stars",
                    callback_data="custom_stars",
                )
            ],
        ]

        await query.edit_message_text(
            "⭐ تعداد Stars را انتخاب کنید:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

        return

    # PAYMENT

    if query.data == "pay":

        code = context.user_data.get("current_order")

        if not code:
            await query.edit_message_text(
                "❌ سفارش فعالی پیدا نشد."
            )
            return

        order = get_order(code)

        if not order:
            await query.edit_message_text(
                "❌ سفارش پیدا نشد."
            )
            return

        # رزرو Stars فقط هنگام ورود به مرحله پرداخت
        if order[7] == "awaiting_payment":
            if not reserve_stars(order[3]):
                await query.edit_message_text(
                    "❌ موجودی Stars برای این سفارش کافی نیست."
                )
                return

            expires_at = (
                datetime.now() + timedelta(minutes=30)
            ).isoformat()

            conn = sqlite3.connect(DB)
            conn.execute(
                """
                UPDATE orders
                SET payment_expires_at=?
                WHERE code=?
                """,
                (expires_at, code),
            )
            conn.commit()
            conn.close()

        elif order[7] == "expired":
            await query.edit_message_text(
                "⏰ مهلت پرداخت این سفارش تمام شده است.\n"
                "لطفاً یک سفارش جدید ثبت کنید."
            )
            return

        recipient = order[8] if len(order) > 8 else ""

        keyboard = [
            [
                InlineKeyboardButton(
                    "💳 شهر بانک",
                    callback_data="card_1",
                )
            ],
            [
                InlineKeyboardButton(
                    "💳 بلو بانک",
                    callback_data="card_2",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔙 بازگشت",
                    callback_data="order_back",
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ لغو سفارش",
                    callback_data="cancel_order",
                )
            ],
        ]

        await query.edit_message_text(
            f"💳 روش پرداخت را انتخاب کنید:\\n\\n"
            f"👤 آیدی مقصد: {recipient}\\n"
            f"⭐ تعداد Stars: {order[3]:,}\\n"
            f"💰 مبلغ: {order[5]:,} تومان",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

        return


    # CARD 1 / CARD 2

    if query.data in ("card_1", "card_2"):

        code = context.user_data.get("current_order")

        if not code:
            await query.edit_message_text(
                "❌ سفارش فعالی پیدا نشد."
            )
            return

        if query.data == "card_1":
            card = CARD_1
            card_name = "شهر بانک"
        else:
            card = CARD_2
            card_name = "بلو بانک"

        update_card(code, card)

        order = get_order(code)
        recipient = order[8] if order and len(order) > 8 else ""
        amount = order[5] if order else 0
        stars = order[3] if order else 0

        context.user_data["waiting_receipt"] = True

        keyboard = [
            [
                InlineKeyboardButton(
                    "📸 ارسال رسید",
                    callback_data="receipt",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔙 بازگشت",
                    callback_data="pay",
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ لغو سفارش",
                    callback_data="cancel_order",
                )
            ],
        ]

        await query.edit_message_text(
            f"💳 {card_name}\n\n"
            f"شماره کارت:\n"
            f"{card}\n\n"
            f"👤 آیدی مقصد: {recipient}\n"
            f"⭐ تعداد Stars: {stars:,}\n"
            f"💰 مبلغ: {amount:,} تومان\n\n"
            "مبلغ سفارش را دقیقاً مطابق مبلغ نمایش‌داده‌شده "
            "پرداخت کنید.\n\n"
            "بعد از پرداخت، تصویر رسید را ارسال کنید.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

        return


    # RECEIPT BUTTON

    if query.data == "receipt":

        context.user_data["waiting_receipt"] = True

        keyboard = [
            [
                InlineKeyboardButton(
                    "🔙 بازگشت",
                    callback_data="pay",
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ لغو سفارش",
                    callback_data="cancel_order",
                )
            ]
        ]

        await query.edit_message_text(
            "📸 لطفاً تصویر رسید پرداخت را همینجا ارسال کنید.\n\n"
            "بعد از ارسال تصویر، رسید برای ادمین فرستاده می‌شود.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

        return



    # ADMIN APPROVE

    if query.data.startswith("approve_"):

        if query.from_user.id != ADMIN_ID:
            return

        code = query.data.replace("approve_", "")

        order = get_order(code)

        if not order:
            await query.answer(
                "سفارش پیدا نشد.",
                show_alert=True,
            )
            return

        if order[7] != "awaiting_review":
            await query.answer(
                "این سفارش قبلاً بررسی شده.",
                show_alert=True,
            )
            return

        stars_amount = order[3]
        product_type = order[9] if len(order) > 9 else "stars"
        product_name = order[10] if len(order) > 10 else None
        product_ton = order[11] if len(order) > 11 else None

        # Stars قبلاً هنگام ورود به مرحله پرداخت رزرو شده‌اند.
        update_status(code, "approved")

        user_id = order[1]

        if product_type == "gift":
            product_text = (
                f"🎁 گیفت: {product_name}\n"
                f"💎 قیمت پایه: {product_ton} TON"
            )
        else:
            product_text = f"⭐ تعداد Stars: {stars_amount:,}"

        delivery_keyboard = [
            [
                InlineKeyboardButton(
                    "⭐ تحویل شد",
                    callback_data=f"deliver_{code}",
                )
            ]
        ]

        await context.bot.send_message(
            chat_id=user_id,
            text=(
                f"✅ پرداخت سفارش {code} تأیید شد.\n\n"
                f"👤 آیدی مقصد: {order[8]}\n"
                f"{product_text}\n\n"
                "⏳ سفارش شما وارد مرحله تحویل شد."
            ),
        )

        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"📦 سفارش آماده تحویل\n\n"
                f"🧾 سفارش: {code}\n"
                f"👤 آیدی مقصد: {order[8]}\n"
                f"{product_text}\n\n"
                "پس از تحویل محصول به آیدی مقصد، "
                "دکمه زیر را بزنید."
            ),
            reply_markup=InlineKeyboardMarkup(delivery_keyboard),
        )

        await query.edit_message_caption(
            caption=query.message.caption + "\n\n✅ تأیید شد."
        )

        return

    # ADMIN DELIVERY
    if query.data.startswith("deliver_"):

        if query.from_user.id != ADMIN_ID:
            return

        code = query.data.replace("deliver_", "")
        order = get_order(code)

        if not order:
            await query.answer(
                "سفارش پیدا نشد.",
                show_alert=True,
            )
            return

        if order[7] != "approved":
            await query.answer(
                "این سفارش در وضعیت قابل تحویل نیست.",
                show_alert=True,
            )
            return

        product_type = order[9] if len(order) > 9 else "stars"
        product_name = order[10] if len(order) > 10 else None
        product_ton = order[11] if len(order) > 11 else None
        stars_amount = order[3]

        # GIFT DELIVERY
        if product_type == "gift":

            update_status(code, "delivered")

            user_id = order[1]

            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    f"🎉 سفارش {code} تحویل داده شد.\n\n"
                    f"🎁 گیفت: {product_name}\n"
                    f"💎 قیمت پایه: {product_ton} TON\n"
                    f"👤 آیدی مقصد: {order[8]}\n\n"
                    "ممنون از خرید شما ❤️"
                ),
            )

            await query.edit_message_text(
                f"✅ تحویل گیفت انجام شد\n\n"
                f"🧾 سفارش: {code}\n"
                f"🎁 گیفت: {product_name}\n"
                f"💎 قیمت پایه: {product_ton} TON\n"
                f"👤 آیدی مقصد: {order[8]}"
            )

            return

        # STARS DELIVERY
        conn = sqlite3.connect(DB)
        row = conn.execute(
            "SELECT total, reserved FROM safebox WHERE id=1"
        ).fetchone()

        if not row:
            conn.close()
            await query.answer(
                "❌ گاوصندوق پیدا نشد.",
                show_alert=True,
            )
            return

        total_stars, reserved_stars = row

        if reserved_stars < stars_amount:
            conn.close()
            await query.answer(
                "❌ موجودی رزرو شده کافی نیست.",
                show_alert=True,
            )
            return

        if total_stars < stars_amount:
            conn.close()
            await query.answer(
                "❌ موجودی گاوصندوق کافی نیست.",
                show_alert=True,
            )
            return

        conn.execute(
            "UPDATE safebox SET total=?, reserved=? WHERE id=1",
            (
                total_stars - stars_amount,
                reserved_stars - stars_amount,
            ),
        )
        conn.commit()
        conn.close()

        update_status(code, "delivered")

        user_id = order[1]

        await context.bot.send_message(
            chat_id=user_id,
            text=(
                f"🎉 سفارش {code} تحویل داده شد.\n\n"
                f"⭐ تعداد Stars: {stars_amount:,}\n"
                f"👤 آیدی مقصد: {order[8]}\n\n"
                "ممنون از خرید شما ❤️"
            ),
        )

        await query.edit_message_text(
            f"✅ تحویل انجام شد\n\n"
            f"🧾 سفارش: {code}\n"
            f"⭐ Stars: {stars_amount:,}\n"
            f"📦 موجودی گاوصندوق: "
            f"{total_stars - stars_amount:,} ⭐"
        )

        return

    # ADMIN REJECT

    if query.data.startswith("reject_"):

        if query.from_user.id != ADMIN_ID:
            return

        code = query.data.replace("reject_", "")

        order = get_order(code)

        if not order:
            await query.answer(
                "سفارش پیدا نشد.",
                show_alert=True,
            )
            return

        if order[7] != "awaiting_review":
            await query.answer(
                "این سفارش قبلاً بررسی شده.",
                show_alert=True,
            )
            return

        # آزاد کردن Stars رزرو شده پس از رد رسید
        release_order_reservation(code)
        update_status(code, "rejected")

        user_id = order[1]

        await context.bot.send_message(
            chat_id=user_id,
            text=(
                f"❌ پرداخت سفارش {code} رد شد.\n\n"
                "لطفاً با پشتیبانی تماس بگیرید."
            ),
        )

        await query.edit_message_caption(
            caption=query.message.caption + "\n\n❌ رد شد."
        )

        return

# ---------------- RECEIPT ----------------

async def receive_receipt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not context.user_data.get("waiting_receipt"):
        return

    if not update.message.photo:

        await update.message.reply_text(
            "❌ لطفاً رسید را به صورت عکس ارسال کنید."
        )

        return

    code = context.user_data.get("current_order")

    if not code:

        await update.message.reply_text(
            "❌ سفارش فعالی پیدا نشد."
        )

        return

    order = get_order(code)

    if not order:

        await update.message.reply_text(
            "❌ سفارش پیدا نشد."
        )

        return

    photo = update.message.photo[-1]

    user = update.effective_user

    product_type = order[9] if len(order) > 9 else "stars"
    product_name = order[10] if len(order) > 10 else None
    product_ton = order[11] if len(order) > 11 else None

    if product_type == "gift":
        product_info = (
            f"🎁 گیفت: {product_name}\n"
            f"💎 قیمت پایه: {product_ton} TON\n"
        )
    else:
        product_info = (
            f"⭐ Stars: {order[3]:,}\n"
            f"💱 نرخ USDT: {order[4]:,.0f} تومان\n"
        )

    keyboard = [
        [
            InlineKeyboardButton(
                "✅ تأیید پرداخت",
                callback_data=f"approve_{code}",
            )
        ],
        [
            InlineKeyboardButton(
                "❌ رد پرداخت",
                callback_data=f"reject_{code}",
            )
        ],
    ]

    await context.bot.send_photo(
        chat_id=ADMIN_ID,
        photo=photo.file_id,
        caption=(
            "📥 رسید پرداخت جدید\n\n"
            f"🧾 سفارش: {code}\n"
            f"👤 سفارش‌دهنده: @{user.username or 'بدون یوزرنیم'}\n"
            f"🆔 ID سفارش‌دهنده: {user.id}\n"
            f"🎯 آیدی گیرنده: {order[8]}\n"
            f"{product_info}"
            f"💰 مبلغ: {order[5]:,} تومان\n"
            f"💳 کارت: {order[6]}"
        ),
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    update_status(code, "awaiting_review")

    context.user_data["waiting_receipt"] = False

    await update.message.reply_text(
        "✅ رسید دریافت شد.\n\n"
        "⏳ رسید برای بررسی ادمین ارسال شد."
    )


# ---------------- PHOTO ROUTER ----------------

async def receive_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):


    if context.user_data.get("waiting_receipt"):
        await receive_receipt(update, context)
        return

    await update.message.reply_text(
        "❌ در حال حاضر منتظر دریافت رسید نیستیم."
    )


# ---------------- CUSTOM STARS INPUT ----------------

async def receive_custom_stars(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not context.user_data.get("waiting_custom_stars"):
        return

    text = update.message.text.strip().replace(",", "")

    if not text.isdigit() or int(text) < 50:
        await update.message.reply_text(
            "❌ تعداد Stars نامعتبر است.\\n\\n"
            "⭐ حداقل: 50 Stars\\n"
            "⭐ حداکثر: 2000 Stars\\n\\n"
            "مثلاً: 750"
        )
        return

    stars = int(text)

    if stars > 2000:
        await update.message.reply_text(
            "❌ حداکثر تعداد قابل خرید ۲۰۰۰ Stars است."
        )
        return

    safe = get_safebox()

    if safe is None:
        await update.message.reply_text(
            "❌ خطا در بررسی موجودی گاوصندوق."
        )
        return

    safe_total, safe_reserved = safe
    available = safe_total - safe_reserved

    if stars > available:
        await update.message.reply_text(
            f"❌ موجودی کافی نیست.\\n\\n"
            f"⭐ موجودی قابل فروش: {available:,} Stars\\n"
            f"⭐ درخواست شما: {stars:,} Stars"
        )
        return

    star_price = get_current_star_price()
    if star_price is None:
        await update.message.reply_text(
            "❌ قیمت Stars هنوز بروزرسانی نشده است.\n"
            "لطفاً چند لحظه بعد دوباره تلاش کنید."
        )
        return

    total = round(stars * star_price)

    context.user_data["waiting_custom_stars"] = False
    context.user_data["waiting_recipient_username"] = True
    context.user_data["pending_stars"] = stars
    context.user_data["pending_total"] = total
    context.user_data["pending_username"] = (
        update.effective_user.username or ""
    )
    context.user_data["pending_user_id"] = update.effective_user.id

    keyboard = [
        [
            InlineKeyboardButton(
                "🔙 بازگشت",
                callback_data="buy",
            )
        ],
        [
            InlineKeyboardButton(
                "❌ لغو سفارش",
                callback_data="cancel_order",
            )
        ]
    ]

    await update.message.reply_text(
        "👤 آیدی تلگرام گیرنده Stars را وارد کنید.\\n\\n"
        "مثال:\\n"
        "@username\\n\\n"
        "⚠️ آیدی باید با @ شروع شود.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ---------------- RECIPIENT USERNAME ----------------

# ---------------- RECIPIENT USERNAME ----------------

async def receive_recipient_username(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not context.user_data.get("waiting_recipient_username"):
        return

    recipient = update.message.text.strip()

    if not recipient.startswith("@") or len(recipient) < 2:
        await update.message.reply_text(
            "❌ آیدی نامعتبر است.\n\n"
            "لطفاً آیدی تلگرام را با @ وارد کنید.\n"
            "مثال: @username"
        )
        return

    stars = context.user_data.get("pending_stars")
    total = context.user_data.get("pending_total")
    username = context.user_data.get("pending_username", "")
    user_id = context.user_data.get("pending_user_id")

    if not all(
        value is not None
        for value in (stars, total, user_id)
    ):
        context.user_data["waiting_recipient_username"] = False

        await update.message.reply_text(
            "❌ اطلاعات سفارش پیدا نشد.\n"
            "لطفاً دوباره سفارش خود را ثبت کنید."
        )
        return

    code = create_order(
        user_id,
        username,
        stars,
        0,
        total,
        recipient,
    )

    context.user_data["waiting_recipient_username"] = False
    context.user_data["current_order"] = code

    keyboard = [
        [
            InlineKeyboardButton(
                "💳 پرداخت",
                callback_data="pay",
            )
        ]
    ]

    await update.message.reply_text(
        f"🧾 سفارش شما ایجاد شد\n\n"
        f"🔢 شماره سفارش: {code}\n"
        f"👤 آیدی گیرنده: {recipient}\n"
        f"⭐ تعداد Stars: {stars:,}\n"
 
        f"💰 مبلغ قابل پرداخت:\n"
        f"{total:,} تومان\n\n"
        f"📌 وضعیت: در انتظار پرداخت",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ---------------- GIFT RECIPIENT USERNAME ----------------

async def receive_gift_recipient(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not context.user_data.get("waiting_gift_recipient"):
        return

    recipient = update.message.text.strip()

    if not recipient.startswith("@") or len(recipient) < 2:
        await update.message.reply_text(
            "❌ آیدی نامعتبر است.\n\n"
            "لطفاً آیدی تلگرام را با @ وارد کنید.\n"
            "مثال: @username"
        )
        return

    gift_name = context.user_data.get("pending_gift_name")
    gift_ton = context.user_data.get("pending_gift_ton")
    total = context.user_data.get("pending_total")
    username = context.user_data.get("pending_username", "")
    user_id = context.user_data.get("pending_user_id")

    if not all(
        value is not None
        for value in (gift_name, gift_ton, total, user_id)
    ):
        context.user_data["waiting_gift_recipient"] = False

        await update.message.reply_text(
            "❌ اطلاعات سفارش پیدا نشد.\n"
            "لطفاً دوباره سفارش خود را ثبت کنید."
        )
        return

    code = create_order(
        user_id,
        username,
        0,
        0,
        total,
        recipient,
    )

    conn = sqlite3.connect(DB)
    conn.execute(
        """
        UPDATE orders
        SET product_type='gift',
            product_name=?,
            product_ton=?
        WHERE code=?
        """,
        (gift_name, gift_ton, code),
    )
    conn.commit()
    conn.close()

    context.user_data["waiting_gift_recipient"] = False
    context.user_data["current_order"] = code

    keyboard = [
        [
            InlineKeyboardButton(
                "💳 پرداخت",
                callback_data="pay",
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 بازگشت",
                callback_data="gifts",
            )
        ],
        [
            InlineKeyboardButton(
                "❌ لغو سفارش",
                callback_data="cancel_order",
            )
        ],
    ]

    await update.message.reply_text(
        f"🧾 سفارش شما ایجاد شد\n\n"
        f"🔢 شماره سفارش: {code}\n"
        f"🎁 محصول: {gift_name}\n"
        f"👤 آیدی گیرنده: {recipient}\n"
        f"💎 قیمت پایه: {gift_ton} TON\n"
        f"💰 مبلغ قابل پرداخت: {total:,} تومان\n\n"
        f"📌 وضعیت: در انتظار پرداخت",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ---------------- NIGHTLY MESSAGES ----------------

async def send_nightly_message(context):
    conn = sqlite3.connect(DB)

    users = conn.execute(
        """
        SELECT user_id
        FROM bot_users
        WHERE night_messages=1
        """
    ).fetchall()

    row = conn.execute(
        """
        SELECT message_index
        FROM night_message_state
        WHERE id=1
        """
    ).fetchone()

    message_index = row[0] if row else 0

    conn.close()

    # Get the message for this night.
    text = get_night_message(message_index)

    if not text:
        print("Nightly message list is empty.")
        return

    keyboard = [
        [
            InlineKeyboardButton(
                "🛍️ فروشگاه محصولات",
                callback_data="nightly_store",
            )
        ],
        [
            InlineKeyboardButton(
                "🔕 لغو پیام‌های شبانه",
                callback_data="disable_nightly",
            )
        ],
    ]

    sent_count = 0

    for (user_id,) in users:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            sent_count += 1

        except Exception as e:
            print(
                f"Nightly message failed for {user_id}: {e}"
            )

    # Move to the next message index.
    # This will be used by the 365-message system.
    conn = sqlite3.connect(DB)

    conn.execute(
        """
        UPDATE night_message_state
        SET message_index=?
        WHERE id=1
        """,
        ((message_index + 1) % 365,),
    )

    conn.commit()
    conn.close()

    print(
        f"Nightly message sent to {sent_count} users."
    )


# ---------------- MAIN ----------------

def main():

    init_db()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CallbackQueryHandler(buttons)
    )

    async def handle_text(update, context):
        if context.user_data.get("waiting_custom_stars"):
            await receive_custom_stars(update, context)
            return

        if context.user_data.get("waiting_gift_recipient"):
            await receive_gift_recipient(update, context)
            return

        if context.user_data.get("waiting_recipient_username"):
            await receive_recipient_username(update, context)
            return


    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text,
        )
    )

    app.add_handler(
        MessageHandler(
            filters.PHOTO,
            receive_photo,
        )
    )

    # ---------------- PRICE UPDATE JOBS ----------------

    # بروزرسانی قیمت‌ها در ساعت 03:00 و 15:00
    app.job_queue.run_daily(
        update_prices,
        time=time(hour=3, minute=0),
        name="price_update_03",
    )

    app.job_queue.run_daily(
        update_prices,
        time=time(hour=15, minute=0),
        name="price_update_15",
    )

    # ---------------- NIGHTLY MESSAGE JOB ----------------

    app.job_queue.run_daily(
        send_nightly_message,
        time=time(
            hour=0,
            minute=0,
            tzinfo=ZoneInfo("Asia/Tehran"),
        ),
        name="nightly_messages",
    )

    # ---------------- SAFEBOX WALLET SYNC ----------------

    # همگام‌سازی موجودی Safebox با کیف پول TON
    app.job_queue.run_repeating(
        sync_safebox_with_wallet,
        interval=300,
        first=10,
        name="sync_safebox_with_wallet",
    )

# ---------------- FRAGMENT PRICE UPDATE JOB ----------------

    async def refresh_fragment_and_safebox(context):
        updated = await update_fragment_price()

        if updated:
            await sync_safebox_with_wallet()

    app.job_queue.run_repeating(
        refresh_fragment_and_safebox,
        interval=21600,
        first=10,
        name="fragment_price_6h",
    )

# ---------------- PAYMENT EXPIRATION JOB ----------------
    app.job_queue.run_repeating(
        expire_payment_orders,
        interval=60,
        first=10,
        name="expire_payment_orders",
    )

    print("Bot is running...")

    app.run_polling()


if __name__ == "__main__":
    main()

