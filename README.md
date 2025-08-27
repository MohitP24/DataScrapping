# MSRIT Results Scraper (Selenium)

## 📌 Overview
This project automates scraping of student results from **MSRIT exam portal** (`https://exam.msrit.edu/`) using **Python + Selenium**.

- Starts at **1MS21AD001** and moves sequentially across **multiple years (21–24)** and **branches (AD, AI, CS, IS, CI, CY)**.
- Scrapes **Normal students** (`001..`) and **Diploma students** (`401..`) under the same **students array**.
- Handles:
  - **CAPTCHA** (user solves manually when prompted).
  - **Timeouts / ambiguous states** → user prompted to re-enter captcha.
  - **"OOPS, NOT FOUND" / "TAL – To be Announced Later"** → treated as *not found*.
  - **Supplementary results** cards (saved as `"Semester": "Supplementary Results"`).
- Output stored in a **nested JSON file** (`results.json` by default).

---

## ⚙️ Requirements
- **Python 3.8+**
- Install dependencies:
  ```bash
  pip install selenium




## Output Format:
{
  "start_time": "2025-08-27 21:45:00",
  "students": [
    {
      "USN": "1MS21CS001",
      "Name": "John Doe",
      "CGPA": 8.23,
      "Semesters": [
        {
          "Semester": 1,
          "SGPA": 8.5,
          "Courses": [
{
              "Course_Code": "21CS11",
             "Course_Name": "Programming in C",
              "Grade": "A",
              "Timestamp": "22:00:05"
            }
          ]
        },
        {
          "Semester": "Supplementary Results",
          "SGPA": null,
          "Courses": [ ... ]
        }
      ]
    }
  ]
}
