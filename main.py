# -*- coding: utf-8 -*-
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
import json
import csv
import smtplib
from email.mime.text import MIMEText
from datetime import datetime
import easyocr
import re
from PIL import Image

API_TOKEN = "8482946212:AAETAUihHQRxFlc0CAzHGKRKZG3xWtgxHuY"
bot = telebot.TeleBot(API_TOKEN)

with open("rules.json", "r", encoding="utf-8") as f:
    rules = json.load(f)

user_states = {}
pending_services = {}
pending_order_ids = {}
user_phone_numbers = {}
pending_claim_texts = {}

def log_request(user_id, service_name, contact_type, timestamp):
    with open("requests_log.csv", mode="a", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow([user_id, service_name, contact_type, timestamp])


def get_service_by_keyword(lines):
    full_text = " ".join([re.sub(r'\s+', ' ', line.lower().strip()) for line in lines])
    for key, rule in rules.items():
        for alias in rule.get("aliases", []):
            if alias.lower() in full_text:
                return rule
    return None

def send_email(to_email, subject, message_body):
    from_email = "returno@ukr.net"
    from_password = "kLyEf84wfnWtVV1z"

    msg = MIMEText(message_body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email

    try:
        with smtplib.SMTP_SSL("smtp.ukr.net", 465) as server:
            server.login(from_email, from_password)
            server.send_message(msg)
        return True
    except Exception as e:
        print("❌ Email error:", e)
        return False

def extract_info_from_text(ocr_texts):
    order_id = ""
    phone = ""
    lines = []

    print("=== OCR розпізнано текст ===")
    for (_, text, _) in ocr_texts:
        print(text)  # ✅ Тут побачимо, чи є слова "нова пошта" тощо
        lines.append(text)

        match_phone = re.search(r"\b(0\d{9})\b", text)
        if match_phone and not phone:
            phone = match_phone.group(1)

        match_order = re.search(r"\b\d{12,14}\b", text.replace(" ", ""))
        if match_order and not order_id:
            order_id = match_order.group(0)


    print("=== Спроба знайти сервіс ===")
    service = get_service_by_keyword(lines)
    print("Знайдено сервіс:", service['name'] if service else "❌ НЕ знайдено")

    return service, order_id, phone

@bot.message_handler(content_types=["photo"])
def handle_photo(message):
    user_id = message.from_user.id
    chat_id = message.chat.id

    file_info = bot.get_file(message.photo[-1].file_id)
    downloaded_file = bot.download_file(file_info.file_path)

    with open("received.jpg", 'wb') as f:
        f.write(downloaded_file)

    reader = easyocr.Reader(['uk', 'en'])
    results = reader.readtext("received.jpg")

    service, order_id, phone = extract_info_from_text(results)

    if not service:
        bot.reply_to(message, "❗️Не вдалося визначити сервіс. Спробуйте ввести вручну.")
        return

    pending_services[user_id] = service
    pending_order_ids[user_id] = order_id
    user_phone_numbers[user_id] = phone
    user_states[user_id] = "processing_claim"
    process_claim(chat_id, user_id, phone)

@bot.message_handler(commands=['start'])
def handle_start(message):
    user_id = message.from_user.id
    user_states[user_id] = "waiting_service"

    bot.set_my_commands([
        telebot.types.BotCommand("start", "Почати роботу"),
        telebot.types.BotCommand("help", "Отримати допомогу"),
        telebot.types.BotCommand("services", "Перелік доступних сервісів")
    ])


    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("📷 Завантажити чек", callback_data="upload_photo"))
    kb.add(InlineKeyboardButton("🔍 Ввести назву сервісу", callback_data="manual_input"))
    
    bot.send_message(
        message.chat.id,
        "👋 Привіт! Я Returno бот. Оберіть спосіб початку:",
        reply_markup=kb
    )


@bot.message_handler(commands=['services'])
def handle_services(message):
    service_list = [f"• {v['name']}" for v in rules.values()]

    if not service_list:
        bot.send_message(message.chat.id, "❗️Список сервісів порожній.")
    else:
        response = "📋 Доступні сервіси:\n\n" + "\n".join(service_list)
        bot.send_message(message.chat.id, response)

@bot.message_handler(func=lambda message: not message.text.startswith("/"))
def handle_text(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    text = message.text.strip()
    state = user_states.get(user_id)

    if text == "✏️ Ввести номер вручну":
        user_states[user_id] = "waiting_phone"
       # bot.send_message(chat_id, "✏️ Введіть номер телефону вручну:")
        return


    if state == "waiting_service":
        service = get_service_by_keyword([text])

        if not service:
            bot.send_message(chat_id, "❌ Сервіс не знайдено. Спробуйте іншу назву.")
            return
        pending_services[user_id] = service
        user_states[user_id] = "waiting_generate"
        refund_policy = service.get("refund_policy", "Немає інформації про політику повернення.")
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("📄 Згенерувати звернення", callback_data="generate_claim"))
        bot.send_message(chat_id, f"📌 Правила повернення коштів для {service['name']}:\n\n{refund_policy}", reply_markup=kb)

    elif state == "waiting_order_id":
        pending_order_ids[user_id] = text
        user_states[user_id] = "waiting_phone_choice"
        kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        kb.add(KeyboardButton("📱 Поділитись номером", request_contact=True))
        kb.add("✏️ Ввести номер вручну", "⏭ Пропустити")
        bot.send_message(chat_id, "📞 Як зручніше надати номер телефону?", reply_markup=kb)


    elif state == "waiting_phone":

        phone = text

        user_phone_numbers[user_id] = phone

        hide_kb = telebot.types.ReplyKeyboardRemove()

        bot.send_message(chat_id, "✅ Номер отримано.", reply_markup=hide_kb)

        if user_states.get(user_id) != "processing_claim":
            user_states[user_id] = "processing_claim"
            process_claim(chat_id, user_id, phone)

    else:
        bot.send_message(chat_id, "❗ Введіть команду /start для початку.")

@bot.message_handler(content_types=["contact"])
def handle_contact(message):
    user_id = message.from_user.id
    phone = message.contact.phone_number
    user_phone_numbers[user_id] = phone
    user_states[user_id] = "processing_claim"
    process_claim(message.chat.id, user_id, phone)

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    data = call.data

    if call.data == "upload_photo":
        # Прибираємо кнопки
        bot.edit_message_reply_markup(chat_id=chat_id, message_id=call.message.message_id, reply_markup=None)

        bot.send_message(chat_id, "📷 Надішліть фото чека.")
        user_states[user_id] = "waiting_photo"

    elif call.data == "manual_input":
        # Прибираємо кнопки
        bot.edit_message_reply_markup(chat_id=chat_id, message_id=call.message.message_id, reply_markup=None)

    elif data == "list_services":
        bot.edit_message_reply_markup(chat_id=chat_id, message_id=call.message.message_id, reply_markup=None)
        service_list = [f"• {v['name']}" for k, v in rules.items()]
        bot.send_message(chat_id, "📋 Доступні сервіси:\n\n" + "\n".join(service_list))

       # bot.send_message(chat_id, "🖊 Введіть назву сервісу.")
        # user_states[user_id] = "waiting_service"

    if data == "generate_claim":
        user_states[user_id] = "waiting_order_id"
        bot.send_message(chat_id, "🔢 Введіть номер замовлення:")


    elif data == "enter_phone_manual":

        if user_states.get(user_id) != "waiting_phone":

            user_states[user_id] = "waiting_phone"

            bot.send_message(chat_id, "✏️ Введіть номер телефону вручну:")

        else:

            bot.answer_callback_query(call.id, text="Ви вже ввели номер вручну.")
    elif data == "skip_phone":
        user_states[user_id] = "processing_claim"
        process_claim(chat_id, user_id, phone="")

    elif data == "send_email":
        claim_text = pending_claim_texts.get(user_id)
        service = pending_services.get(user_id)
        contact_email = service.get("contact_email") if service else None

        if not claim_text or not contact_email:
            bot.send_message(chat_id, "⚠️ Немає всіх даних для надсилання. Спробуйте спочатку.")
            return

        success = send_email(
            to_email=contact_email,
            subject=f"Звернення від користувача {user_id}",
            message_body=claim_text
        )
        # ⛔ Прибрати попередні inline-кнопки (редагувати повідомлення з кнопками)
        try:
            bot.edit_message_reply_markup(chat_id=chat_id, message_id=call.message.message_id, reply_markup=None)
        except Exception as e:
            print("⚠️ Неможливо прибрати кнопки:", e)

        if success:
            bot.send_message(chat_id, "✅ Email успішно надіслано з бота!")

            user_states.pop(user_id, None)
            pending_services.pop(user_id, None)
            pending_order_ids.pop(user_id, None)
            user_phone_numbers.pop(user_id, None)
            pending_claim_texts.pop(user_id, None)
        else:
            bot.send_message(chat_id, "❌ Сталася помилка при надсиланні email.")

def process_claim(chat_id, user_id, phone):
    service = pending_services.get(user_id)
    order_id = pending_order_ids.get(user_id)

    if not service or not order_id:
        bot.send_message(chat_id, "⚠️ Сталася помилка. Спробуйте /start спочатку.")
        return

    service_name = service["name"]
    contact_type = service.get("contact_type")
    contact_url = service.get("contact_url")
    contact_email = service.get("contact_email")
    claim_template = service.get("claim_template", "Тут буде ваш шаблон звернення.")
    refund_policy = service.get("refund_policy", "Немає інформації про політику повернення.")

    claim_text = claim_template.replace("{order_id}", order_id).replace("{phone}", phone)
    pending_claim_texts[user_id] = claim_text

    bot.send_message(chat_id, f"📌 *Правила повернення коштів для* _{service_name}_:\n\n{refund_policy}",
                     parse_mode="Markdown")
    bot.send_message(chat_id, f"📝 *Звернення для* _{service_name}_:\n\n{claim_text}", parse_mode="Markdown")

    kb = InlineKeyboardMarkup()
    if contact_email:
        kb.add(InlineKeyboardButton("📤 Надіслати з бота", callback_data="send_email"))
    if contact_url:
        kb.add(InlineKeyboardButton("🌐 Відкрити форму", url=contact_url))

    if kb.keyboard:
        bot.send_message(chat_id, "⬇️ Доступні дії:", reply_markup=kb)

    log_request(user_id, service_name, contact_type, datetime.now().isoformat())

    # Оновлюємо стан, щоб не дублювати
    user_states[user_id] = "claim_sent"

if __name__ == "__main__":
    print("🤖 Returno бот запущено та чекає на команди...")
    bot.infinity_polling()