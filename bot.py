import time
import json
import os
import sqlite3
import requests
from cryptography.fernet import Fernet
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

# Selenium
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service

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

# ---------- SELENIUM ----------
def init_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options
    )
    return driver

def login_and_fetch(username, password):
    driver = init_driver()
    updates = []

    try:
        driver.get(MOODLE_URL + "/login/index.php")

        driver.find_element(By.NAME, "username").send_keys(username)
        driver.find_element(By.NAME, "password").send_keys(password)
        driver.find_element(By.ID, "loginbtn").click()

        time.sleep(3)

        if "login" in driver.current_url:
            print(f"❌ فشل تسجيل الدخول: {username}")
            driver.quit()
            return None

        print(f"✅ تسجيل دخول ناجح: {username}")

        courses = driver.find_elements(By.CSS_SELECTOR, ".coursebox a, .card a")

        for course in courses:
            try:
                title = course.text.strip()
                link = course.get_attribute("href")

                driver.get(link)
                time.sleep(2)

                activities = driver.find_elements(By.CSS_SELECTOR, ".activityinstance a")

                for act in activities:
                    text = act.text.strip()
                    href = act.get_attribute("href")

                    if text:
                        updates.append(f"{title}\n{text}\n{href}")

            except:
                continue

        driver.quit()
        return list(set(updates))

    except Exception as e:
        print("Selenium error:", e)
        driver.quit()
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

                    print(f"🔍 محاولة تسجيل دخول: {username}")

                    result = login_and_fetch(username, password)

                    if result is None:
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

        # CHECK UPDATES
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

                diff = [u for u in new_updates if u not in data.get("last_seen", [])]

                for item in diff:
                    send_message(chat_id, f"تحديث جديد:\n{item}")

                if diff:
                    users[chat_id]["last_seen"].extend(diff)
                    save_user(chat_id)

        time.sleep(5)

    except Exception as e:
        print("Main error:", e)
        time.sleep(10)
