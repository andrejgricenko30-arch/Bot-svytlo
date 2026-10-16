


CHECK_INTERVAL = 300
HTTP_TIMEOUT = (5, 15)

if not BOT_TOKEN or BOT_TOKEN.startswith("ВСТАВ_"):
    raise RuntimeError("Вкажи свій поточний BOT_TOKEN")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("yasno_bot")

bot = telebot.TeleBot(
    BOT_TOKEN,
    threaded=True,
    num_threads=4
)

app = Flask(__name__)

UA_DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]

last_sent_status = None
monitor_lock = threading.Lock()


# ==========================================
# RENDER WEB SERVICE
# ==========================================

@app.route("/")
def index():
    return "YASNO Telegram Bot is running!", 200


@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200


def run_web():
    port = int(os.environ.get("PORT", "10000"))
    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False,
        threaded=True
    )


# ==========================================
# ФОРМАТУВАННЯ ГРАФІКІВ
# ==========================================

def minutes_to_time(minutes):
    minutes = int(minutes)

    if not 0 <= minutes <= 1440:
        raise ValueError("Некоректне значення часу")

    if minutes == 1440:
        return "24:00"

    return f"{minutes // 60}:{minutes % 60:02d}"


def format_day_schedule(day_offset, day_data):
    if not isinstance(day_data, dict):
        return ""

    slots = day_data.get("slots")
    if not isinstance(slots, list):
        return ""

    target_date = (
        datetime.datetime.now(KYIV_TZ).date()
        + datetime.timedelta(days=day_offset)
    )

    day_name = UA_DAYS[target_date.weekday()]
    label = "сьогодні" if day_offset == 0 else "завтра"

    lines = [
        f"<b>Відключення групи {MY_GROUP} "
        f"{label} ({day_name}):</b>"
    ]

    has_outages = False

    for slot in slots:
        if not isinstance(slot, dict):
            continue

        if slot.get("type") not in ("Definite", "Possible"):
            continue

        try:
            start = minutes_to_time(slot["start"])
            end = minutes_to_time(slot["end"])
        except (KeyError, TypeError, ValueError):
            log.warning("Пропущено некоректний інтервал")
            continue

        lines.append(f"🌧️ {start} - {end}")
        has_outages = True

    if not has_outages:
        lines.append("☀️ Відключень не планується")

    return "\n".join(lines)


# ==========================================
# ОТРИМАННЯ ГРАФІКІВ YASNO
# ==========================================

def get_yasno_schedule():
    try:
        response = requests.get(
            YASNO_URL,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=HTTP_TIMEOUT
        )
        response.raise_for_status()
        data = response.json()

    except requests.RequestException:
        log.exception("Помилка запиту до YASNO")
        return (
            "❌ Не вдалося отримати графік YASNO. "
            "Спробуйте пізніше."
        )

    except ValueError:
        log.exception("YASNO повернув некоректний JSON")
        return "❌ Сервіс YASNO повернув некоректні дані."

    if not isinstance(data, dict):
        return "❌ Неочікуваний формат відповіді YASNO."

    if MY_GROUP not in data:
        return (
            f"⚠️ YASNO не повернув дані для групи {MY_GROUP}.\n"
            "Це не означає, що відключень немає."
        )

    schedule = data[MY_GROUP]

    if not isinstance(schedule, dict):
        return "⚠️ Дані графіка тимчасово недоступні."

    today = schedule.get("today")
    tomorrow = schedule.get("tomorrow")

    for day_data in (today, tomorrow):
        if (
            isinstance(day_data, dict)
            and day_data.get("status") == "EmergencyShutdowns"
        ):
            return (
                "🚨 <b>Екстрені відключення!</b>\n"
                "Планові графіки можуть не діяти."
            )

    parts = []

    if isinstance(today, dict):
        result = format_day_schedule(0, today)
        if result:
            parts.append(result)

    if isinstance(tomorrow, dict):
        result = format_day_schedule(1, tomorrow)
        if result:
            parts.append(result)

    if not parts:
        return (
            "⚠️ YASNO поки не надав графік. "
            "Перевірте пізніше."
        )

    return "\n\n".join(parts)


# ==========================================
# TELEGRAM: ВІДПРАВЛЕННЯ
# ==========================================

def send_text(chat_id, text, html=True):
    try:
        bot.send_message(
            chat_id,
            text,
            parse_mode="HTML" if html else None
        )
        return True

    except Exception:
        log.exception(
            "Помилка надсилання повідомлення до чату %s",
            chat_id
        )
        return False


def answer_schedule(message):
    log.info("Запит графіка від chat_id=%s", message.chat.id)

    try:
        bot.send_chat_action(message.chat.id, "typing")
    except Exception:
        log.warning("Не вдалося надіслати chat action")

    text = get_yasno_schedule()
    send_text(message.chat.id, text)


# ==========================================
# КОМАНДИ TELEGRAM
# ==========================================

@bot.message_handler(commands=["start", "light"])
def command_schedule(message):
    answer_schedule(message)


@bot.message_handler(commands=["debug"])
def command_debug(message):
    try:
        response = requests.get(
            YASNO_URL,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=HTTP_TIMEOUT
        )

        debug_text = (
            f"HTTP статус: {response.status_code}\n\n"
            f"Відповідь YASNO:\n{response.text[:3000]}"
        )

        send_text(message.chat.id, debug_text, html=False)

    except Exception as exc:
        log.exception("Помилка команди debug")
        send_text(
            message.chat.id,
            f"Помилка debug: {exc}",
            html=False
        )


@bot.message_handler(
    content_types=["text"],
    func=lambda message: bool(
        message.text and re.match(
            r"^/світло(?:@\w+)?(?:\s|$)",
            message.text.strip(),
            re.IGNORECASE
        )
    )
)
def command_light_ukrainian(message):
    answer_schedule(message)


# ==========================================
# АВТОМАТИЧНИЙ МОНІТОРИНГ
# ==========================================

def monitor_status():
    global last_sent_status

    log.info("Моніторинг YASNO запущено")

    # Часові позначки обов'язкових зведень.
    # Зберігаються в пам'яті до перезапуску процесу.
    daily_sent_slots = set()

    while True:
        try:
            current_text = get_yasno_schedule()
            now = datetime.datetime.now(KYIV_TZ)

            # ======================================
            # 1. ЗВЕДЕННЯ О 15:00 ТА 21:00
            # ======================================

            for target_hour in (15, 21):
                slot_key = (now.date(), target_hour)

                if (
                    now.hour == target_hour
                    and slot_key not in daily_sent_slots
                ):
                    # Якщо YASNO підтвердив екстрений статус,
                    # тут буде повідомлення про екстрені відключення.
                    # Якщо даних немає, надсилаємо попередження.
                    if send_text(CHAT_ID, current_text):
                        daily_sent_slots.add(slot_key)
                        log.info(
                            "Обов'язкове зведення за %02d:00 надіслано",
                            target_hour
                        )
                    else:
                        log.warning(
                            "Зведення за %02d:00 не надіслано. "
                            "Повторна спроба під час наступної перевірки",
                            target_hour
                        )

            # ======================================
            # 2. НЕ РОБИМО ВИСНОСКІВ ЗА ПОМИЛКАМИ
            # ======================================

            if current_text.startswith(("❌", "⚠️")):
                log.warning(
                    "Дані YASNO не підтверджені. "
                    "Попередній стан зберігаємо"
                )

            else:
                with monitor_lock:

                    # ======================================
                    # 3. ПЕРШИЙ УСПІШНИЙ ЗАПИТ
                    # ======================================

                    if last_sent_status is None:

                        if current_text.startswith("🚨"):
                            # Якщо бот стартував під час екстрених
                            # відключень, повідомляємо про них.
                            if send_text(CHAT_ID, current_text):
                                last_sent_status = current_text
                                log.info(
                                    "Надіслано початкове повідомлення "
                                    "про екстрені відключення"
                                )
                        else:
                            # Звичайний графік запам'ятовуємо,
                            # не надсилаючи зайвого повідомлення
                            # відразу після запуску.
                            last_sent_status = current_text
                            log.info("Початковий графік збережено")

                    # ======================================
                    # 4. ЗМІНА СТАНУ
                    # ======================================

                    elif current_text != last_sent_status:

                        previous_emergency = (
                            last_sent_status.startswith("🚨")
                        )
                        current_emergency = (
                            current_text.startswith("🚨")
                        )

                        if current_emergency:
                            notification = current_text

                        elif previous_emergency:
                            notification = (
                                "✅ <b>Екстрені відключення завершились!</b>\n\n"
                                "Актуальний графік YASNO:\n\n"
                                + current_text
                            )

                        else:
                            notification = (
                                "⚡ <b>Зміна графіка світла!</b>\n\n"
                                + current_text
                            )

                        # Оновлюємо стан тільки після успішної
                        # відправки повідомлення.
                        if send_text(CHAT_ID, notification):
                            last_sent_status = current_text
                            log.info("Зміну графіка надіслано")
                        else:
                            log.warning(
                                "Не вдалося повідомити про зміну. "
                                "Повторимо спробу"
                            )

            # Видаляємо часові позначки попередніх днів.
            daily_sent_slots = {
                key for key in daily_sent_slots
                if key[0] >= now.date()
            }

        except Exception:
            log.exception("Помилка моніторингу")

        time.sleep(CHECK_INTERVAL)


# ==========================================
# ЗАПУСК
# ==========================================

def main():
    threading.Thread(
        target=run_web,
        daemon=True,
        name="web-server"
    ).start()

    threading.Thread(
        target=monitor_status,
        daemon=True,
        name="yasno-monitor"
    ).start()

    try:
        bot.remove_webhook()
        log.info("Webhook видалено")
    except Exception:
        log.exception("Не вдалося видалити webhook")

    while True:
        try:
            log.info("Запуск Telegram polling")

            bot.infinity_polling(
                timeout=20,
                long_polling_timeout=15,
                skip_pending=False
            )

        except Exception:
            log.exception(
                "Помилка Telegram polling. Повтор через 5 секунд"
            )
            time.sleep(5)


if __name__ == "__main__":
    main()
