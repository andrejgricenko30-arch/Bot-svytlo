import os
import requests
import telebot
import datetime
import time
import threading
from flask import Flask

# Налаштування бота
BOT_TOKEN = "8780180824:AAFo8ZsKrGp7KXffpTnVYt6eJc3n1Oi2g6I"
CHAT_ID = "-1003957769146"
MY_GROUP = "1.1"

bot = telebot.TeleBot(BOT_TOKEN)
UA_DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]

# Змінна для збереження попереднього стану, щоб не спамити однаковими повідомленнями
last_sent_status = None

# Веб-сервер для підтримки активності сервісу
app = Flask(__name__)

@app.route('/')
def index():
    return "Бот YASNO працює 24/7!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

def minutes_to_time(minutes):
    h = minutes // 60
    m = minutes % 60
    return f"{h}:{m:02d}"

def format_day_schedule(day_offset, day_data):
    if not day_data or 'slots' not in day_data:
        return ""
        
    target_date = datetime.datetime.utcnow() + datetime.timedelta(hours=3, days=day_offset)
    day_str = UA_DAYS[target_date.weekday()]
    
    title = f"Відключення групи {MY_GROUP} {'сьогодні' if day_offset == 0 else 'завтра'} ({day_str}):"
    text = f"<b>{title}</b>\n"
    has_outages = False
    
    for slot in day_data['slots']:
        if slot['type'] in ["Definite", "Possible"]:
            start = minutes_to_time(slot['start'])
            end = minutes_to_time(slot['end'])
            text += f"    🔴 {start} - {end}\n"
            has_outages = True
            
    if not has_outages:
        text += "    🟢 Відключень не планується\n"
        
    return text + "\n"

def get_yasno_schedule():
    url = "https://app.yasno.ua/api/blackout-service/public/shutdowns/regions/25/dsos/902/planned-outages"
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            
            if not data or MY_GROUP not in data:
                return "⚠️ <b>Увага!</b> Стабілізаційні графіки наразі не діють."
            
            schedule_data = data[MY_GROUP]
            
            if not schedule_data or ('today' not in schedule_data and 'tomorrow' not in schedule_data):
                return "⚠️ <b>Увага!</b> Планові графіки тимчасово недоступні."

            # Перевірка на екстрені відключення
            today_status = schedule_data.get('today', {}).get('status', '')
            tomorrow_status = schedule_data.get('tomorrow', {}).get('status', '')
            
            if today_status == "EmergencyShutdowns" or tomorrow_status == "EmergencyShutdowns":
                return "🚨 <b>Увага! Екстрені відключення, графіки не діють.</b>"

            final_text = ""
            if 'today' in schedule_data and schedule_data['today']:
                final_text += format_day_schedule(0, schedule_data['today'])
            if 'tomorrow' in schedule_data and schedule_data['tomorrow']:
                final_text += format_day_schedule(1, schedule_data['tomorrow'])
                
            if not final_text.strip():
                return "⚠️ <b>Увага!</b> Графіки тимчасово не відображаються."
                
            return final_text.strip()
        else:
            return f"❌ Помилка доступу до сервісу YASNO: {response.status_code}"
    except Exception as e:
        return f"❌ Виникла помилка при отриманні графіків: {e}"

@bot.message_handler(commands=['start', 'світло', 'light'])
def send_schedule(message):
    text = get_yasno_schedule()
    try:
        bot.send_message(message.chat.id, text, parse_mode='HTML')
    except Exception as e:
        print(f"Помилка відправки HTML: {e}")
        try:
            bot.send_message(message.chat.id, f"Помилка форматування повідомлення. Сирі дані:\n\n{text}")
        except Exception as e2:
            print(f"Критична помилка відправки: {e2}")

@bot.message_handler(commands=['debug'])
def send_debug(message):
    url = "https://app.yasno.ua/api/blackout-service/public/shutdowns/regions/25/dsos/902/planned-outages"
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=10)
        bot.send_message(message.chat.id, f"<code>{resp.text[:4000]}</code>", parse_mode='HTML')
    except Exception as e:
        bot.send_message(message.chat.id, f"Помилка debug: {e}")

def monitor_status():
    global last_sent_status
    while True:
        try:
            current_text = get_yasno_schedule()
            
            # Якщо текст статусу змінився (наприклад, з графіків на екстрені або навпаки)
            if current_text != last_sent_status:
                # Перевіряємо, чи це не помилка мережі, щоб не спамити у разі збою інтернету
                if not current_text.startswith("❌"):
                    bot.send_message(CHAT_ID, f"⚡ <b>Зміна статусу світла:</b>\n\n{current_text}", parse_mode='HTML')
                    last_sent_status = current_text
        except Exception as e:
            print(f"Помилка в фоновому моніторингу: {e}")
            
        # Перевіряємо сайт кожні 5 хвилин (300 секунд)
        time.sleep(300)

def run_bot():
    bot.infinity_polling()

if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    threading.Thread(target=monitor_status, daemon=True).start()
    run_bot()
