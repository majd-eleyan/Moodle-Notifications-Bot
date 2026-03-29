import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service

MOODLE_URL = "https://moodle.alaqsa.edu.ps"

def init_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    return webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options
    )

def check_moodle(username, password):
    driver = init_driver()
    updates = []

    try:
        driver.get(MOODLE_URL + "/login/index.php")

        driver.find_element(By.NAME, "username").send_keys(username)
        driver.find_element(By.NAME, "password").send_keys(password)
        driver.find_element(By.ID, "loginbtn").click()

        time.sleep(3)

        if "login" in driver.current_url:
            driver.quit()
            return None

        print(f"Login OK: {username}")

        courses = driver.find_elements(By.CSS_SELECTOR, ".coursebox a, .card a")

        for course in courses:
            try:
                title = course.text.strip()
                link = course.get_attribute("href")

                driver.get(link)
                time.sleep(2)

                sections = driver.find_elements(By.CSS_SELECTOR, "li[id^='section-']")

                for sec in sections:
                    activities = sec.find_elements(By.CSS_SELECTOR, ".activityinstance a")

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
        print("Moodle error:", e)
        driver.quit()
        return None
