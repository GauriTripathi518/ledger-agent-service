import io
import json
import sqlite3
import requests
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from google import genai

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Put your Gemini API Key here:
GEMINI_API_KEY = "AQ.Ab8RN6LCHj61tlRy1tjhwEldGVlQAsNv4itk2d9_9iJniR2UnQ"

EXPORT_URL = "https://exam.sanand.workers.dev/questionData?email=23f2001555%40ds.study.iitm.ac.in&quizSign=xXlju3jeJXd3w91X4LtqY5Ga%2FbpFQO%2F0cJoPGCREt0l57XB17cMHkd0KlgA%2F8yUur1gz8jPfbQ2zDBQ5fIA%2FP5Pa97Ssx4BvH6QzNF53Q2iau9IrKEHA9%2BKQw4K2fOq9uUCeisT1ix297gT9D7xerumDgMVTVXAXd0aPUKFMKuHP0Q4goaoEdHcqnlUD3i0cvHFeAS1PosGnvNnjnAH2Afh1%2FE5JPge1vd0CdJP%2BYM27%2BMSCFjn9Gpbm%2Big%2FXmke%2BvazpT4p431lslg1Sut4aGipAdExbj0p7mSnyNP%2FdN3t6x0VEiVjdUovjP8ig7h%2FpreMLOZlwLacMD3rPoN%2FSOZ7q%2Bs9pDyzye9PQ%3D%3D&questionId=q-ledger-agent-server&path=%2Fexport"
RATES_URL = "https://exam.sanand.workers.dev/questionData?email=23f2001555%40ds.study.iitm.ac.in&quizSign=xXlju3jeJXd3w91X4LtqY5Ga%2FbpFQO%2F0cJoPGCREt0l57XB17cMHkd0KlgA%2F8yUur1gz8jPfbQ2zDBQ5fIA%2FP5Pa97Ssx4BvH6QzNF53Q2iau9IrKEHA9%2BKQw4K2fOq9uUCeisT1ix297gT9D7xerumDgMVTVXAXd0aPUKFMKuHP0Q4goaoEdHcqnlUD3i0cvHFeAS1PosGnvNnjnAH2Afh1%2FE5JPge1vd0CdJP%2BYM27%2BMSCFjn9Gpbm%2Big%2FXmke%2BvazpT4p431lslg1Sut4aGipAdExbj0p7mSnyNP%2FdN3t6x0VEiVjdUovjP8ig7h%2FpreMLOZlwLacMD3rPoN%2FSOZ7q%2Bs9pDyzye9PQ%3D%3D&questionId=q-ledger-agent-server&path=%2Frates"

db_conn = None

def init_db():
    global db_conn
    db_conn = sqlite3.connect(":memory:", check_same_thread=False)
    cursor = db_conn.cursor()

    try:
        r_rates = requests.get(RATES_URL, timeout=10).json()
        rates = r_rates.get("usd_per_unit", {"USD": 1.0, "EUR": 1.13, "INR": 0.01244})
    except Exception:
        rates = {"USD": 1.0, "EUR": 1.13, "INR": 0.01244}

    resp = requests.get(EXPORT_URL, timeout=30)
    raw_lines = resp.text.strip().split("\n")

    latest_records = {}
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            oid = row["id"]
            if oid not in latest_records or row.get("updated_at", "") > latest_records[oid].get("updated_at", ""):
                latest_records[oid] = row
        except Exception:
            continue

    cursor.execute("""
        CREATE TABLE orders (
            id TEXT PRIMARY KEY,
            customer TEXT,
            region TEXT,
            product TEXT,
            qty INTEGER,
            unit_price REAL,
            amount REAL,
            currency TEXT,
            status TEXT,
            created_at TEXT,
            updated_at TEXT,
            amount_usd REAL,
            order_date TEXT,
            order_year INTEGER,
            order_month INTEGER
        )
    """)

    rows_to_insert = []
    for r in latest_records.values():
        curr = r.get("currency", "USD")
        rate = rates.get(curr, 1.0)
        amt = float(r.get("amount", 0.0))
        amt_usd = round(amt * rate, 2)

        ts = r.get("created_at", "")
        year, month = 2026, 1
        if len(ts) >= 10:
            date_str = ts[:10]
            try:
                parts = date_str.split("-")
                year = int(parts[0])
                month = int(parts[1])
            except Exception:
                pass
        else:
            date_str = ""

        rows_to_insert.append((
            r.get("id"),
            r.get("customer"),
            r.get("region"),
            r.get("product"),
            int(r.get("qty", 1)),
            float(r.get("unit_price", 0.0)),
            amt,
            curr,
            r.get("status"),
            r.get("created_at"),
            r.get("updated_at"),
            amt_usd,
            date_str,
            year,
            month
        ))

    cursor.executemany("""
        INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, rows_to_insert)
    db_conn.commit()

init_db()

@app.post("/answer")
async def answer_question(request: Request):
    data = await request.json()
    question = data.get("question", "")

    client = genai.Client(api_key=GEMINI_API_KEY)

    prompt = f"""
You are a SQLite expert. Given the table `orders`:
Columns:
- id TEXT
- customer TEXT
- region TEXT (Central, East, South, North)
- product TEXT (e.g. Blender, Mixer, Toaster, Air Fryer)
- qty INTEGER
- unit_price REAL
- amount REAL
- currency TEXT (USD, EUR, INR)
- status TEXT (paid, void)
- created_at TEXT
- updated_at TEXT
- amount_usd REAL (amount converted to USD)
- order_date TEXT (YYYY-MM-DD)
- order_year INTEGER (e.g. 2026)
- order_month INTEGER (1 to 12)

BUSINESS RULES:
1. "revenue" or "earned" ONLY counts orders where `status = 'paid'`.
2. All monetary questions ask about USD, so use `amount_usd`.
3. If money/revenue is asked, sum `amount_usd` and round to 2 decimal places: `ROUND(SUM(amount_usd), 2)`.
4. If asked "Which product...", SELECT ONLY the product name column.
5. If asked "Which customer...", SELECT ONLY the customer column.
6. Return ONLY the raw SQL query inside ```sql ... ``` code block. No extra words or commentary.

Question: {question}
"""

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )
    sql_text = response.text
    if "```sql" in sql_text:
        sql = sql_text.split("```sql")[1].split("```")[0].strip()
    elif "```" in sql_text:
        sql = sql_text.split("```")[1].split("```")[0].strip()
    else:
        sql = sql_text.strip()

    cursor = db_conn.cursor()
    cursor.execute(sql)
    result = cursor.fetchone()

    val = result[0] if result else None
    if isinstance(val, float):
        val = round(val, 2)

    return {"answer": val}
