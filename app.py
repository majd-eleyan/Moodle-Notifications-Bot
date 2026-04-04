import time
import json
import os
import sqlite3
import requests
from cryptography.fernet import Fernet
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
from bs4 import BeautifulSoup

# ---------------- CONFIG ----------------
TOKEN = os.getenv("TOKEN")
MOODLE_URL = "https://moodle.alaqsa.edu.ps"

cipher = Fernet(os.getenv("SECRET_KEY").encode())

# ---------- DATABASE ----------
conn = sqlite3.connect("users.db", check_same_thread=False)
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

users = {}

def load_users():
    global users
    users = {}
    cursor.execute("SELECT * FROM users")
    for row in cursor.fetchall():
        chat_id, username, password, step, last_seen = row
        users[chat_id] = {
            "username": username,
            "password": password,
            "step": step,
            "last_seen": json.loads(last_seen) if last_seen else []
        }

def save_user(chat_id):
    data = users[chat_id]
    cursor.execute("""
    INSERT OR REPLACE INTO users VALUES (?, ?, ?, ?, ?)
    """, (
        chat_id,
        data.get("username"),
        data.get("password"),
        data.get("step"),
        json.dumps(data.get("last_seen", []))
    ))
    conn.commit()

# ---------- TELEGRAM ----------
def send_message(chat_id, text):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data={"chat_id": chat_id, "text": text},
            timeout=10
        )
    except:
        pass

def get_updates(offset=None):
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TOKEN}/getUpdates",
            params={"offset": offset},
            timeout=10
        ).json()
        return r if r.get("ok") else {"result": []}
    except:
        return {"result": []}

# ---------- MOODLE LOGIN ----------
def login_and_fetch(username, password):
    session = requests.Session()

    try:
        # 1. GET login page
        r = session.get(MOODLE_URL + "/login/index.php")
        soup = BeautifulSoup(r.text, "html.parser")

        token_input = soup.find("input", {"name": "logintoken"})
        logintoken = token_input["value"] if token_input else ""

        # 2. POST login
        payload = {
            "username": username,
            "password": password,
            "logintoken": logintoken
        }

        login = session.post(MOODLE_URL + "/login/index.php", data=payload)

        # 3. تحقق من النجاح
        if "loginerrors" in login.text or "Invalid login" in login.text:
            print(f"❌ فشل تسجيل الدخول: {username}")
            return None

        print(f"✅ تسجيل دخول ناجح: {username}")

        updates = []

        # 4. ادخل Dashboard
        dash = session.get(MOODLE_URL + "/my/")
        soup = BeautifulSoup(dash.text, "html.parser")

        courses = soup.select("a[href*='course/view']")

        for c in courses:
            title = c.text.strip()
            link = c.get("href")

            if not title or not link:
                continue

            try:
                course_page = session.get(link)
                course_soup = BeautifulSoup(course_page.text, "html.parser")

                activities = course_soup.select(".activityinstance a")

                for act in activities:
                    name = act.text.strip()
                    href = act.get("href")

                    if name:
                        updates.append(f"{title}\n{name}\n{href}")

            except:
                continue

        return list(set(updates))

    except Exception as e:
        print("Request error:", e)
        return None

# ---------- SERVER ----------
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"running")

def run_server():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()

threading.Thread(target=run_server, daemon=True).start()

# ---------- MAIN ----------
load_users()
print("Bot started")

last_update_id = None
last_check_time = 0

while True:
    try:
        updates = get_updates(last_update_id)

        for update in updates.get("result", []):
            last_update_id = update["update_id"] + 1
            message = update.get("message")
            if not message:
                continue

            chat_id = str(message["chat"]["id"])
            text = message.get("text", "")

            if text == "/start":
                users[chat_id] = {"step": "username"}
                send_message(chat_id, "أدخل الرقم الجامعي")

            elif chat_id in users:

                if users[chat_id]["step"] == "username":
                    users[chat_id]["username"] = text
                    users[chat_id]["step"] = "password"
                    send_message(chat_id, "أدخل كلمة السر")

                elif users[chat_id]["step"] == "password":

                    username = users[chat_id]["username"]
                    password = text

                    result = login_and_fetch(username, password)

                    if result is None:
                        send_message(chat_id, "❌ بيانات غير صحيحة")
                        users[chat_id]["step"] = "username"
                        continue

                    encrypted = cipher.encrypt(password.encode()).decode()

                    users[chat_id]["password"] = encrypted
                    users[chat_id]["step"] = "done"
                    users[chat_id]["last_seen"] = []

                    save_user(chat_id)

                    send_message(chat_id, "✅ تم التسجيل بنجاح")

                else:
                    send_message(chat_id, "🤖 البوت يعمل")

            else:
                send_message(chat_id, "اكتب /start")

        # ---------- CHECK UPDATES ----------
        if time.time() - last_check_time > 120:
            last_check_time = time.time()

            for chat_id, data in users.items():
                if data.get("step") != "done":
                    continue

                username = data["username"]
                password = cipher.decrypt(data["password"].encode()).decode()

                new_updates = login_and_fetch(username, password)

                if not new_updates:
                    continue

                old = data.get("last_seen", [])
                diff = [u for u in new_updates if u not in old]

                for item in diff:
                    send_message(chat_id, f"📢 تحديث جديد:\n{item}")

                if diff:
                    users[chat_id]["last_seen"].extend(diff)
                    save_user(chat_id)

        time.sleep(2)

    except Exception as e:
        print("Main error:", e)
        time.sleep(5)
