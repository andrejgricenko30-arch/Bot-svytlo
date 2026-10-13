import os
import requests
import telebot
import datetime
import time
import threading
from flask import Flask

# Настройки бота
BOT_TOKEN = "8780180824:AAFo8ZsKrGp7KXffpTnVYt6eJc3n1Oi2g6I"
CHAT_ID = "-1003957769146"
MY_GROUP = "1.1"

bot = telebot.TeleBot(BOT_TOKEN)
UA_DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]

# Веб-сервер для поддержки активности сервиса
app = Flask(__name__)

@app.route('/')
def index():
    return "Бот YASNO работает 24/7!"

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
            if MY_GROUP in data:
                schedule_data = data[MY_GROUP]
                final_text = ""
                if 'today' in schedule_data:
                    final_text += format_day_schedule(0, schedule_data['today'])
                if 'tomorrow' in schedule_data:
                    final_text += format_day_schedule(1, schedule_data['tomorrow'])
                return final_text.strip()
            else:
                return f"❌ Дані для групи {MY_GROUP} не знайдено."
        else:
            return f"❌ Помилка доступу: {response.status_code}"
    except Exception as e:
        return f"❌ Виникла помилка: {e}"

@bot.message_handler(commands=['start', 'світло', 'light'])
def send_schedule(message):
    try:
        bot.send_message(message.chat.id, get_yasno_schedule(), parse_mode='HTML')
    except Exception as e:
        pass

def auto_sender():
    send_times = ["08:00", "15:00", "21:00"]
    
    while True:
        now = (datetime.datetime.utcnow() + datetime.timedelta(hours=3)).strftime("%H:%M")
        if now in send_times:
            try:
                bot.send_message(CHAT_ID, get_yasno_schedule(), parse_mode='HTML')
            except Exception as e:
                pass
            time.sleep(61)
        time.sleep(1)

def run_bot():
    bot.infinity_polling()

if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    threading.Thread(target=auto_sender, daemon=True).start()
    run_bot()
