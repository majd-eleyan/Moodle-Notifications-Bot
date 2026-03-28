import time
import json
import os
import re
import requests
import sqlite3
from cryptography.fernet import Fernet
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

# -------------- CONFIG -----------------
TOKEN = os.getenv("TOKEN")
WELCOME_MSG = "البوت شغال ، أي تحديث جديد سيصلك مباشرة"
MOODLE_URL = "https://moodle.alaqsa.edu.ps"

if not TOKEN:
    raise Exception("TOKEN is missing")

secret = os.getenv("SECRET_KEY")
if not secret:
    raise Exception("SECRET_KEY is missing")

cipher = Fernet(secret.encode())

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
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=10)
    except Exception as e:
        print("Send error:", e)

def get_updates(offset=None):
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
        r = requests.get(url, params={"offset": offset}, timeout=10).json()
        if not r.get("ok"):
            return {"result": []}
        return r
    except:
        return {"result": []}

# ---------- MOODLE SESSION ----------
def login_and_get_session(username, password):
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0",
        "Referer": MOODLE_URL + "/login/index.php"
    })

    try:
        # جلب صفحة الدخول لاستخراج logintoken
        resp = session.get(MOODLE_URL + "/login/index.php", timeout=10)
        token_match = re.search(r'name="logintoken" value="([^"]+)"', resp.text)
        logintoken = token_match.group(1) if token_match else ""

        payload = {
            "username": username,
            "password": password,
            "logintoken": logintoken
        }

        session.post(MOODLE_URL + "/login/index.php", data=payload, timeout=10)

        dash = session.get(MOODLE_URL + "/my/", timeout=10)

        if "login" in dash.url or "login" in dash.text.lower():
            return None

        return session

    except Exception as e:
        print("Login error:", e)
        return None

# ---------- FETCH UPDATES ----------
def fetch_moodle_updates(username, password):
    session = login_and_get_session(username, password)
    if not session:
        return []

    updates = []

    try:
        dashboard = session.get(MOODLE_URL + "/my/", timeout=10)

        # استخراج روابط المساقات
        course_links = re.findall(
            r'href="(https://moodle\.alaqsa\.edu\.ps/course/view\.php\?id=\d+)"',
            dashboard.text
        )
        course_links = list(set(course_links))

        for course_url in course_links:
            try:
                course_page = session.get(course_url, timeout=10)

                # اسم المساق
                title_match = re.search(r'<title>(.*?)</title>', course_page.text, re.IGNORECASE)
                course_title = title_match.group(1).strip() if title_match else "Course"

                # الأنشطة
                activities = re.findall(
                    r'<a[^>]+href="(https://moodle\.alaqsa\.edu\.ps/mod/[^"]+)"[^>]*>(.*?)</a>',
                    course_page.text,
                    re.DOTALL
                )

                for link, raw_text in activities:
                    text = re.sub("<.*?>", "", raw_text).strip()
                    if text:
                        updates.append(f"{course_title} - {text} - {link}")

            except:
                continue

        return list(set(updates))

    except Exception as e:
        print("Fetch error:", e)
        return []

# ---------- SERVER (Render) ----------
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

for chat_id in users:
    send_message(chat_id, WELCOME_MSG)

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

                    # نجرب تسجيل الدخول
                    session = login_and_get_session(username, password)

                    if not session:
                         print(f"❌ فشل تسجيل الدخول: {username}")
                         send_message(chat_id, "بيانات غير صحيحة، حاول مرة أخرى")
                         users[chat_id]["step"] = "username"
                         continue

                    # نجح تسجيل الدخول
                    print(f"✅ تسجيل دخول ناجح: {username}")

                    encrypted = cipher.encrypt(password.encode()).decode()

                    users[chat_id]["password"] = encrypted
                    users[chat_id]["step"] = "done"
                    users[chat_id]["last_seen"] = []

                    save_user(chat_id)

                    send_message(chat_id, "تم التسجيل بنجاح")
                else:
                    send_message(chat_id, WELCOME_MSG)

            else:
                send_message(chat_id, "اكتب /start")

        # فحص التحديثات
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
                    send_message(chat_id, item)

                if diff:
                    users[chat_id]["last_seen"].extend(diff)
                    save_user(chat_id)

        time.sleep(1)

    except Exception as e:
        print("Main loop error:", e)
        time.sleep(3)
