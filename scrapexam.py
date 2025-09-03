#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MSRIT Results Scraper - Selenium (multi-year / multi-branch, nested JSON)
- Starts at 1MS21AD001 and moves forward as per plan:
    Branch order: AD, AI, CS, IS, CI, CY
    Year   order: 21, 22, 23, 24
  For each (year, branch) we scrape:
    • Normal students: 001.. (stop after MAX_CONSEC_OOPS continuous OOPS)
    • Diploma students: (year+1, same branch) 401.. (also stop after MAX_CONSEC_OOPS continuous OOPS)
- User solves CAPTCHA manually on first USN; session is reused where possible.
- Timeout handling: timeout is NOT treated as OOPS — after retries it will prompt you to re-enter captcha.
- JSON output: single "students" array (contains both normal and diploma USNs).
"""

import time
import re
import json
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

# --- CONFIG ---
BASE_URL = "https://exam.msrit.edu/"
OUTFILE_DEFAULT = "results.json"

YEARS = ["21", "22", "23", "24"]
BRANCHES = ["AD", "AI", "CS", "IS", "CI", "CY"]

# Start roll
ROLL_START = 1

# Diploma rolls (join 2nd year). Year used = int(normal_year)+1, rolls 401+
DIP_START = 401

MAX_CONSEC_OOPS = 5   # stop each track after this many consecutive OOPS
RETRY_ON_TIMEOUT = 2  # number of times to retry ambiguous/timeout states before prompting captcha
# ----------------


# ---------- Helpers ----------
def wait(driver, secs=12):
    return WebDriverWait(driver, secs)


def set_input_value_js(driver, element, value):
    driver.execute_script(
        """
        const el = arguments[0], v = arguments[1];
        el.focus();
        el.value = '';
        el.value = v;
        el.dispatchEvent(new Event('input', {bubbles:true}));
        el.dispatchEvent(new Event('change', {bubbles:true}));
        """,
        element,
        value,
    )


def page_has_oops(driver):
    src = driver.page_source.lower()
    return ("oops!!! your usn could not be found" in src) or \
           ("could not be found in our result database" in src) or \
           ("your usn could not be found" in src)


def go_back_to_usn_entry_keep_session(driver):
    # Try history back twice
    for _ in range(2):
        try:
            driver.back()
            wait(driver, 6).until(EC.presence_of_element_located((By.ID, "usn")))
            return True
        except TimeoutException:
            time.sleep(0.3)
    # Try 'click here' / 'try again'
    try:
        link = driver.find_element(
            By.XPATH,
            "//a[contains(., 'click here') or contains(., 'try again') or contains(., 'Click here')]"
        )
        link.click()
        wait(driver, 6).until(EC.presence_of_element_located((By.ID, "usn")))
        return True
    except Exception:
        pass
    # Last resort: full reload (captcha likely resets)
    try:
        driver.get(BASE_URL)
        wait(driver, 10).until(EC.presence_of_element_located((By.ID, "usn")))
        return True
    except Exception:
        return False


def click_go_button(driver):
    selectors = [
        (By.XPATH, "//input[@value='GO' or @value='Go' or @value='go']"),
        (By.XPATH, "//input[@type='submit']"),
        (By.XPATH, "//button[contains(., 'GO') or contains(., 'Go') or contains(., 'Submit')]"),
        (By.ID, "btn7"),
    ]
    for by, sel in selectors:
        try:
            el = driver.find_element(by, sel)
            driver.execute_script("arguments[0].click();", el)
            return True
        except Exception:
            continue
    # Fallback: press Enter on USN input
    try:
        u = driver.find_element(By.ID, "usn")
        u.send_keys(Keys.RETURN)
        return True
    except Exception:
        return False


def wait_for_either(driver, timeout=12):
    """
    Wait for either:
      - OOPS text
      - Semester cards (.cn-result-card)
      - Direct result header/table presence
    Returns a string indicator: "oops" | "cards" | "table" | "timeout"
    """
    end = time.time() + timeout
    while time.time() < end:
        # check OOPS
        if page_has_oops(driver):
            return "oops"
        # cards?
        if driver.find_elements(By.CSS_SELECTOR, ".cn-result-card"):
            return "cards"
        # header/table?
        if driver.find_elements(By.CSS_SELECTOR, "div.student-header p") or \
           driver.find_elements(By.CSS_SELECTOR, "table.uk-table.uk-table-striped.res-table tbody"):
            return "table"
        time.sleep(0.20)
    return "timeout"


def get_semester_cards(driver):
    els = driver.find_elements(By.CSS_SELECTOR, ".uk-card.uk-card-default.uk-card-body.cn-card")
    if els:
        return els
    return driver.find_elements(By.CSS_SELECTOR, ".cn-result-card")


def extract_student_name(driver):
    try_selectors = [
        (By.CSS_SELECTOR, "div.stu-data h3"),
        (By.CSS_SELECTOR, "div.stu-data h2"),
        (By.CSS_SELECTOR, "div.stu-data.stu-data2 h2"),
        (By.CSS_SELECTOR, "div.student-header h2"),
        (By.CSS_SELECTOR, "div.student-header h3"),
        (By.XPATH, "//h3[normalize-space() and string-length(normalize-space())>2]"),
    ]
    for by, sel in try_selectors:
        try:
            els = driver.find_elements(by, sel)
            for e in els:
                t = e.text.strip()
                if t and t.lower() not in ("semester", "result", "exam"):
                    return t
        except Exception:
            continue
    return "Name Not Found"


def extract_semester_number_from_header(driver):
    try:
        p = driver.find_element(By.CSS_SELECTOR, "div.student-header p")
        txt = p.text.strip()
        m = re.search(r"Semester\s*(\d+)", txt, flags=re.IGNORECASE)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return None


def extract_sgpa_from_caption(driver):
    try:
        cap = driver.find_element(By.CSS_SELECTOR, "table.uk-table.uk-table-striped.res-table caption")
        txt = cap.text.strip()
        m = re.search(r"SGPA[:\s]*([\d.]+)", txt)
        if m:
            return float(m.group(1))
        try:
            span = cap.find_element(By.CSS_SELECTOR, "span.uk-label")
            mm = re.search(r"([\d.]+)", span.text)
            if mm:
                return float(mm.group(1))
        except Exception:
            pass
    except Exception:
        pass
    return None


def extract_cgpa_if_any(driver):
    xps = [
        "//p[contains(., 'CGPA')]",
        "//div[contains(., 'CGPA')]",
        "//td[contains(., 'CGPA')]/following-sibling::td[1]",
    ]
    for xp in xps:
        try:
            el = driver.find_element(By.XPATH, xp)
            m = re.search(r"CGPA[:\s]*([\d.]+)", el.text)
            if m:
                return float(m.group(1))
            mm = re.search(r"([\d.]+)", el.text)
            if mm:
                val = float(mm.group(1))
                if 0 <= val <= 10:
                    return val
        except Exception:
            continue
    return None


def extract_courses_from_visible_table(driver):
    courses = []
    try:
        tbody = wait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "table.uk-table.uk-table-striped.res-table tbody"))
        )
        rows = tbody.find_elements(By.TAG_NAME, "tr")
        for r in rows:
            tds = r.find_elements(By.TAG_NAME, "td")
            if len(tds) >= 5:
                code = tds[0].text.strip()
                name = tds[1].text.strip()
                grade = tds[4].text.strip()
                if code and name:
                    courses.append({
                        "Course_Code": code,
                        "Course_Name": name,
                        "Grade": grade,
                        "Timestamp": datetime.now().strftime("%H:%M:%S"),
                    })
    except Exception:
        pass
    return courses
# ---------------------------------------------


# ---------- Scrape CURRENT page (already past GO) into structured semesters ----------
def scrape_current_usn_view_structured(driver, usn):
    """
    Returns tuple:
      (found_any: bool, name: str|None, cgpa: float|None, semesters: list[dict])
    Each semester dict: {"Semester": int|None, "SGPA": float|None, "Courses": [ {...}, ... ]}
    """
    if page_has_oops(driver):
        return (False, None, None, [])

    name = extract_student_name(driver)
    semesters = []
    cgpa_final = None

    # Try semester cards flow
    cards = get_semester_cards(driver)
    if cards:
        for idx in range(len(cards)):
            cards = get_semester_cards(driver)
            if idx >= len(cards):
                break
            card = cards[idx]

            # Find view button inside card
            view_btn = None
            try:
                view_btn = card.find_element(By.CSS_SELECTOR, "input[value='View Results']")
            except Exception:
                try:
                    view_btn = card.find_element(By.XPATH, ".//button[contains(., 'View Results') or contains(., 'VIEW RESULTS')]")
                except Exception:
                    cands = card.find_elements(By.XPATH, ".//a|.//button|.//input")
                    view_btn = cands[0] if cands else None

            if not view_btn:
                continue

            try:
                driver.execute_script("arguments[0].click();", view_btn)
            except Exception:
                continue

            # Wait for result page
            ind = wait_for_either(driver, timeout=10)
            if ind == "oops":
                # unexpected OOPS for a card — skip back and continue
                go_back_to_usn_entry_keep_session(driver)
                continue

            sem_num = extract_semester_number_from_header(driver)
            sgpa = extract_sgpa_from_caption(driver)
            cgpa = extract_cgpa_if_any(driver)
            if cgpa is not None:
                cgpa_final = cgpa

            courses = extract_courses_from_visible_table(driver)
            if courses:
                semesters.append({
                    "Semester": sem_num,
                    "SGPA": sgpa,
                    "Courses": courses
                })

            # Back to cards view
            try:
                driver.back()
                wait(driver, 8).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".cn-result-card")))
                time.sleep(0.15)
            except Exception:
                # try to recover to USN input; break out to avoid loop
                go_back_to_usn_entry_keep_session(driver)
                break

        # After cards processing return to USN entry
        go_back_to_usn_entry_keep_session(driver)
        return (len(semesters) > 0, name, cgpa_final, semesters)

    # Direct results (no cards)
    indicator_present = driver.find_elements(By.CSS_SELECTOR, "div.student-header p") or \
                        driver.find_elements(By.CSS_SELECTOR, "table.uk-table.uk-table-striped.res-table tbody")
    if indicator_present:
        sem_num = extract_semester_number_from_header(driver)
        sgpa = extract_sgpa_from_caption(driver)
        cgpa = extract_cgpa_if_any(driver)
        if cgpa is not None:
            cgpa_final = cgpa
        courses = extract_courses_from_visible_table(driver)
        if courses:
            semesters.append({
                "Semester": sem_num,
                "SGPA": sgpa,
                "Courses": courses
            })
        go_back_to_usn_entry_keep_session(driver)
        return (len(semesters) > 0, name, cgpa_final, semesters)

    return (False, name, cgpa_final, semesters)


# ---------- Submit USN and collect structured result with retries ----------
def submit_and_collect_usn(driver, usn):
    """
    Returns same tuple as scrape_current_usn_view_structured()
    or (False, None, None, []) on explicit OOPS or after user captcha prompt exhaustion.
    """
    # Ensure on USN page
    try:
        wait(driver, 6).until(EC.presence_of_element_located((By.ID, "usn")))
    except TimeoutException:
        go_back_to_usn_entry_keep_session(driver)
        wait(driver, 8).until(EC.presence_of_element_located((By.ID, "usn")))

    # Fill USN
    try:
        u = driver.find_element(By.ID, "usn")
    except Exception:
        return (False, None, None, [])
    set_input_value_js(driver, u, usn)
    time.sleep(0.15)

    # Click GO
    if not click_go_button(driver):
        print("Could not click GO programmatically. Please click GO manually on the page, then press ENTER here.")
        input("Press ENTER after clicking GO...")
    time.sleep(0.5)

    # If captcha visible again, ask to solve
    try:
        if driver.find_elements(By.ID, "captcha"):
            print("[!] Captcha appears again. Please solve it on the site for this USN, click GO, then press ENTER.")
            input("Press ENTER after solving captcha & clicking GO...")
    except Exception:
        pass

    # Retry loop for ambiguous/timeouts
    for attempt in range(RETRY_ON_TIMEOUT + 1):
        ind = wait_for_either(driver, timeout=12)
        if ind == "oops":
            # explicit OOPS page
            return (False, None, None, [])
        elif ind in ("cards", "table"):
            # Good: scrape and return
            return scrape_current_usn_view_structured(driver, usn)
        else:
            # timeout / ambiguous
            if attempt < RETRY_ON_TIMEOUT:
                print(f"[WARN] Ambiguous / timeout for {usn} (attempt {attempt+1}/{RETRY_ON_TIMEOUT}). Retrying submit...")
                # Re-submit (refill + click)
                try:
                    set_input_value_js(driver, u, usn)
                    click_go_button(driver)
                    time.sleep(0.8)
                except Exception:
                    time.sleep(0.8)
                # also check if captcha reappeared
                try:
                    if driver.find_elements(By.ID, "captcha"):
                        print("[!] Captcha reappeared while retrying. Please solve it & click GO, then press ENTER.")
                        input("Press ENTER after solving captcha & clicking GO...")
                except Exception:
                    pass
                continue
            else:
                # exhausted automatic retries -> ask user to solve captcha and continue
                print(f"[INFO] Automatic retries exhausted for {usn}. This is NOT counted as OOPS yet.")
                print("[ACTION] Please check the browser, solve the CAPTCHA if visible, click GO on the page, then press ENTER here to continue.")
                input("Press ENTER after solving captcha & clicking GO (or press ENTER to skip)...")

                # Give user a longer wait for the page to settle
                ind2 = wait_for_either(driver, timeout=30)
                if ind2 in ("cards", "table"):
                    return scrape_current_usn_view_structured(driver, usn)
                elif ind2 == "oops":
                    return (False, None, None, [])
                else:
                    # After user prompt still nothing -> treat as not found (OOPS-like)
                    print(f"[WARN] After manual captcha attempt, {usn} still had no results. Treating as not found.")
                    return (False, None, None, [])

    # fallback
    return (False, None, None, [])


# ---------- Student container helpers ----------
def ensure_student(record_map, usn, name=None, cgpa=None):
    """
    record_map: dict[USN] -> student_object
    Ensures a student object with nested structure exists and merges name/cgpa if they arrive later.
    """
    if usn not in record_map:
        record_map[usn] = {
            "USN": usn,
            "Name": name or "",
            "CGPA": cgpa if cgpa is not None else None,
            "Semesters": []
        }
    else:
        if name and (not record_map[usn].get("Name") or record_map[usn]["Name"] == "Name Not Found"):
            record_map[usn]["Name"] = name
        if cgpa is not None:
            record_map[usn]["CGPA"] = cgpa
    return record_map[usn]


def merge_semesters(student_obj, new_semesters):
    """
    Merge semesters by 'Semester' number; append non-duplicates.
    """
    idx_by_sem = {s.get("Semester"): i for i, s in enumerate(student_obj["Semesters"])}
    for sem in new_semesters:
        s_no = sem.get("Semester")
        if s_no in idx_by_sem:
            dest = student_obj["Semesters"][idx_by_sem[s_no]]
            if dest.get("SGPA") is None and sem.get("SGPA") is not None:
                dest["SGPA"] = sem["SGPA"]
            existing = {(c.get("Course_Code"), c.get("Grade"), c.get("Course_Name")) for c in dest.get("Courses", [])}
            for c in sem.get("Courses", []):
                key = (c.get("Course_Code"), c.get("Grade"), c.get("Course_Name"))
                if key not in existing:
                    dest.setdefault("Courses", []).append(c)
        else:
            student_obj["Semesters"].append(sem)


# ---------- Main ----------
def main():
    start_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"=== MSRIT Selenium Scraper (Start: {start_ts}) ===\n")
    print("=== MSRIT Selenium Scraper (Years 21–24, Branches AD/AI/CS/IS/CI/CY | Single students array) ===\n")
    outfile = input(f"Output JSON filename (default {OUTFILE_DEFAULT}): ").strip() or OUTFILE_DEFAULT

    # Launch browser
    options = webdriver.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    driver = webdriver.Chrome(options=options)
    driver.maximize_window()
    driver.get(BASE_URL)

    # Single container for all students (normal + diploma combined)
    students_map = {}

    try:
        # Build plan: for each branch, iterate years 21->24
        plan = [(y, b) for b in BRANCHES for y in YEARS]

        first_year, first_branch = plan[0]
        first_usn = f"1MS{first_year}{first_branch}{ROLL_START:03d}"

        # Prefill first USN so user can solve CAPTCHA and click GO manually
        try:
            usn_input = wait(driver, 12).until(EC.presence_of_element_located((By.ID, "usn")))
            set_input_value_js(driver, usn_input, first_usn)
            print(f"[ACTION] Prefilled first USN: {first_usn}")
            print("Please solve the CAPTCHA on the site and CLICK GO manually for this first USN.")
            input("After you click GO on the site, press ENTER here to begin automation...")
        except Exception:
            print("Could not prefill USN automatically. Make sure the page is loaded.")
            input("Solve captcha on the page and click GO for the first USN, then press ENTER here...")

        # Handle first manual submit
        print(f"\n=== Processing first (manual) {first_usn} ===")
        ind = wait_for_either(driver, timeout=12)
        if ind == "oops":
            print(f"{first_usn} -> OOPS on first submit (manual). Proceeding.")
            go_back_to_usn_entry_keep_session(driver)
        elif ind in ("cards", "table"):
            found, name, cgpa, semesters = scrape_current_usn_view_structured(driver, first_usn)
            if found:
                stu = ensure_student(students_map, first_usn, name, cgpa)
                merge_semesters(stu, semesters)
                print(f"Completed {first_usn}.")
            else:
                print(f"{first_usn} -> No data scraped.")
        else:
            print(f"{first_usn} -> Timeout after manual submit. Proceeding.")
            go_back_to_usn_entry_keep_session(driver)

        # Iterate plan
        for (year, branch) in plan:
            print(f"\n=== YEAR {year} | BRANCH {branch} (NORMAL) ===")
            consec_oops = 0
            roll = ROLL_START
            # Continue until MAX_CONSEC_OOPS reached
            while True:
                usn = f"1MS{year}{branch}{roll:03d}"
                print(f"--- Processing {usn} ---")
                try:
                    found, name, cgpa, semesters = submit_and_collect_usn(driver, usn)
                except Exception as e:
                    print(f"Error while processing {usn}: {e}")
                    try:
                        go_back_to_usn_entry_keep_session(driver)
                    except Exception:
                        pass
                    roll += 1
                    continue

                if not found:
                    consec_oops += 1
                    print(f"{usn} -> OOPS or no data ({consec_oops}/{MAX_CONSEC_OOPS})")
                    if consec_oops >= MAX_CONSEC_OOPS:
                        print(f"[STOP] {MAX_CONSEC_OOPS} continuous OOPS for {year}{branch} (normal).")
                        go_back_to_usn_entry_keep_session(driver)
                        break
                    go_back_to_usn_entry_keep_session(driver)
                else:
                    consec_oops = 0
                    stu = ensure_student(students_map, usn, name, cgpa)
                    merge_semesters(stu, semesters)
                    time.sleep(0.12)
                roll += 1

            # Diploma track for same branch, year+1 but store into the same students_map
            try:
                year_int = int(year)
            except Exception:
                year_int = 0
            dip_year = f"{year_int + 1:02d}"

            print(f"\n=== YEAR {dip_year} | BRANCH {branch} (DIPLOMA) ===")
            consec_oops_dip = 0
            roll = DIP_START
            while True:
                usn = f"1MS{dip_year}{branch}{roll:03d}"
                print(f"--- Processing {usn} (Diploma) ---")
                try:
                    found, name, cgpa, semesters = submit_and_collect_usn(driver, usn)
                except Exception as e:
                    print(f"Error while processing (Diploma) {usn}: {e}")
                    try:
                        go_back_to_usn_entry_keep_session(driver)
                    except Exception:
                        pass
                    roll += 1
                    continue

                if not found:
                    consec_oops_dip += 1
                    print(f"{usn} -> OOPS or no data ({consec_oops_dip}/{MAX_CONSEC_OOPS}) [Diploma]")
                    if consec_oops_dip >= MAX_CONSEC_OOPS:
                        print(f"[STOP] {MAX_CONSEC_OOPS} continuous OOPS for {dip_year}{branch} (diploma).")
                        go_back_to_usn_entry_keep_session(driver)
                        break
                    go_back_to_usn_entry_keep_session(driver)
                else:
                    consec_oops_dip = 0
                    stu = ensure_student(students_map, usn, name, cgpa)
                    merge_semesters(stu, semesters)
                    time.sleep(0.12)
                roll += 1

        print("\nAll year/branch iterations completed.")

    finally:
        # Save results
        try:
            payload = {
                "start_time": start_ts,
                "students": list(students_map.values())
            }
            with open(outfile, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            print(f"\nSaved results to {outfile}: {len(payload['students'])} students.")
            if payload["students"]:
                print("Sample student:\n", json.dumps(payload["students"][0], indent=2, ensure_ascii=False))
        except Exception as e:
            print("Failed to save results:", e)

        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()


