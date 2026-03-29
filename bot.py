import time
import json
import os
import re
import requests
import sqlite3
from cryptography.fernet import Fernet
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

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

# ---------- USERS ----------
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
    INSERT OR REPLACE INTO users (chat_id, username, password, step, last_seen)
    VALUES (?, ?, ?, ?, ?)
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
    except Exception as e:
        print("Send error:", e)

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

# ---------- LOGIN ----------
def login_and_get_session(username, password):
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0",
        "Referer": MOODLE_URL + "/login/index.php"
    })

    try:
        login_page = session.get(MOODLE_URL + "/login/index.php", timeout=10)

        token_match = re.search(r'name="logintoken" value="([^"]+)"', login_page.text)
        token = token_match.group(1) if token_match else ""

        payload = {
            "username": username,
            "password": password,
            "logintoken": token
        }

        session.post(MOODLE_URL + "/login/index.php", data=payload, timeout=10)

        dash = session.get(MOODLE_URL + "/my/", timeout=10)

        if "login" in dash.url or "login" in dash.text.lower():
            print(f"❌ فشل تسجيل الدخول: {username}")
            return None

        print(f"✅ تسجيل دخول ناجح: {username}")
        return session

    except Exception as e:
        print("Login error:", e)
        return None

# ---------- FETCH ----------
def fetch_moodle_updates(username, password):
    session = login_and_get_session(username, password)
    if not session:
        return []

    updates = []

    try:
        dashboard = session.get(MOODLE_URL + "/my/", timeout=10)

        course_links = re.findall(
            r'href="(https://moodle\.alaqsa\.edu\.ps/course/view\.php\?id=\d+)"',
            dashboard.text
        )

        for course_url in set(course_links):
            try:
                page = session.get(course_url, timeout=10)

                # اسم المساق
                title_match = re.search(r'<title>(.*?)</title>', page.text, re.IGNORECASE)
                course_title = title_match.group(1).strip() if title_match else "مساق"

                # الأنشطة
                activities = re.findall(
                    r'<a[^>]+href="(https://moodle\.alaqsa\.edu\.ps/mod/[^"]+)"[^>]*>(.*?)</a>',
                    page.text,
                    re.DOTALL
                )

                for href, raw in activities:
                    text = re.sub("<.*?>", "", raw).strip()

                    if text and len(text) > 3:
                        updates.append(f"{course_title}\n{text}\n{href}")

            except:
                continue

        return list(set(updates))

    except Exception as e:
        print("Fetch error:", e)
        return []

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

                    print(f"🔍 محاولة تسجيل دخول: {username}")

                    session = login_and_get_session(username, password)

                    if not session:
                        send_message(chat_id, "بيانات غير صحيحة")
                        users[chat_id]["step"] = "username"
                        continue

                    encrypted = cipher.encrypt(password.encode()).decode()

                    users[chat_id]["password"] = encrypted
                    users[chat_id]["step"] = "done"
                    users[chat_id]["last_seen"] = []

                    save_user(chat_id)

                    send_message(chat_id, "تم التسجيل بنجاح")

                else:
                    send_message(chat_id, "البوت يعمل")

            else:
                send_message(chat_id, "اكتب /start")

        # ---------- CHECK ----------
        if time.time() - last_check_time > 60:
            last_check_time = time.time()

            for chat_id, data in users.items():
                if data.get("step") != "done":
                    continue

                username = data["username"]
                password = cipher.decrypt(data["password"].encode()).decode()

                new_updates = fetch_moodle_updates(username, password)

                diff = [u for u in new_updates if u not in data.get("last_seen", [])]

                for item in diff:
                    send_message(chat_id, f"تحديث جديد:\n{item}")

                if diff:
                    users[chat_id]["last_seen"].extend(diff)
                    save_user(chat_id)

        time.sleep(2)

    except Exception as e:
        print("Main error:", e)
        time.sleep(5)
