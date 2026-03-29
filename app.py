import os
import time
import json
import sqlite3
import threading
from flask import Flask, request
import requests
from cryptography.fernet import Fernet
from moodle import check_moodle

# CONFIG
TOKEN = os.getenv("TOKEN")
SECRET_KEY = os.getenv("SECRET_KEY")
cipher = Fernet(SECRET_KEY.encode())

app = Flask(__name__)

# DATABASE
conn = sqlite3.connect("database.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    chat_id TEXT PRIMARY KEY,
    username TEXT,
    password TEXT,
    step TEXT,
    last_seen TEXT
)
""")
conn.commit()

# TELEGRAM
def send(chat_id, text):
    requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": chat_id, "text": text}
    )

# WEBHOOK
@app.route("/", methods=["POST"])
def webhook():
    data = request.json
    message = data.get("message", {})
    chat_id = str(message.get("chat", {}).get("id"))
    text = message.get("text", "")

    cursor.execute("SELECT * FROM users WHERE chat_id=?", (chat_id,))
    user = cursor.fetchone()

    if text == "/start":
        cursor.execute("REPLACE INTO users VALUES (?,?,?,?,?)",
                       (chat_id, "", "", "username", "[]"))
        conn.commit()
        send(chat_id, "أدخل الرقم الجامعي")

    elif user:
        step = user[3]

        if step == "username":
            cursor.execute("UPDATE users SET username=?, step='password' WHERE chat_id=?",
                           (text, chat_id))
            conn.commit()
            send(chat_id, "أدخل كلمة السر")

        elif step == "password":
            username = user[1]
            password = text

            result = check_moodle(username, password)

            if result is None:
                send(chat_id, "بيانات غير صحيحة")
                return "ok"

            enc = cipher.encrypt(password.encode()).decode()

            cursor.execute("""
            UPDATE users SET password=?, step='done', last_seen=?
            WHERE chat_id=?
            """, (enc, json.dumps(result), chat_id))
            conn.commit()

            send(chat_id, "تم التسجيل بنجاح")

        else:
            send(chat_id, "البوت يعمل")

    else:
        send(chat_id, "اكتب /start")

    return "ok"

# BACKGROUND CHECKER
def checker():
    while True:
        cursor.execute("SELECT * FROM users WHERE step='done'")
        users = cursor.fetchall()

        for u in users:
            chat_id, username, enc_pass, _, last_seen = u

            password = cipher.decrypt(enc_pass.encode()).decode()
            last_seen = json.loads(last_seen)

            new = check_moodle(username, password)

            if not new:
                continue

            diff = [x for x in new if x not in last_seen]

            for item in diff:
                send(chat_id, f"تحديث جديد:\n{item}")

            if diff:
                cursor.execute("UPDATE users SET last_seen=? WHERE chat_id=?",
                               (json.dumps(new), chat_id))
                conn.commit()

        time.sleep(300)

threading.Thread(target=checker, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
