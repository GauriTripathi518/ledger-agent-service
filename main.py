import os
import json
import sqlite3
import requests
import traceback
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
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

EXPORT_URL = "https://exam.sanand.workers.dev/questionData?email=23f2001555%40ds.study.iitm.ac.in&quizSign=xXlju3jeJXd3w91X4LtqY5Ga%2FbpFQO%2F0cJoPGCREt0l57XB17cMHkd0KlgA%2F8yUur1gz8jPfbQ2zDBQ5fIA%2FP5Pa97Ssx4BvH6QzNF53Q2iau9IrKEHA9%2BKQw4K2fOq9uUCeisT1ix297gT9D7xerumDgMVTVXAXd0aPUKFMKuHP0Q4goaoEdHcqnlUD3i0cvHFeAS1PosGnvNnjnAH2Afh1%2FE5JPge1vd0CdJP%2BYM27%2BMSCFjn9Gpbm%2Big%2FXmke%2BvazpT4p431lslg1Sut4aGipAdExbj0p7mSnyNP%2FdN3t6x0VEiVjdUovjP8ig7h%2FpreMLOZlwLacMD3rPoN%2FSOZ7q%2Bs9pDyzye9PQ%3D%3D&questionId=q-ledger-agent-server&path=%2Fexport"

DB_PATH = "/tmp/orders.db"

def ensure_database():
    if os.path.exists(DB_PATH) and os.path.getsize(DB_PATH) > 1000:
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
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

    rates = {"USD": 1.0, "EUR": 1.13, "INR": 0.01244}

    resp = requests.get(EXPORT_URL, timeout=15)
    latest_records = {}
    for line in resp.text.strip().split("\n"):
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

    rows = []
    for r in latest_records.values():
        curr = r.get("currency", "USD")
        rate = rates.get(curr, 1.0)
        amt = float(r.get("amount", 0.0))
        amt_usd = round(amt * rate, 2)

        ts = r.get("created_at", "")
        year, month, date_str = 2026, 1, ""
        if len(ts) >= 10:
            date_str = ts[:10]
            try:
                parts = date_str.split("-")
                year = int(parts[0])
                month = int(parts[1])
            except Exception:
                pass

        rows.append((
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

    cursor.executemany("INSERT OR REPLACE INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()

@app.post("/answer")
async def answer_question(request: Request):
    try:
        data = await request.json()
        question = data.get("question", "")

        ensure_database()

        api_key = os.getenv("GEMINI_API_KEY", "")
        client = genai.Client(api_key=api_key)

        prompt = f"""
Given table `orders`:
Columns:
- id TEXT
- customer TEXT
- region TEXT ('Central', 'East', 'South', 'North')
- product TEXT ('Blender', 'Mixer', 'Toaster', 'Air Fryer', 'Rice Cooker', etc.)
- qty INTEGER
- unit_price REAL
- amount REAL
- currency TEXT
- status TEXT ('paid', 'void')
- created_at TEXT
- updated_at TEXT
- amount_usd REAL
- order_date TEXT (YYYY-MM-DD)
- order_year INTEGER
- order_month INTEGER (1 to 12)

RULES:
1. "revenue", "earned", or "paid order" means `status = 'paid'`.
2. For money/revenue in USD, return `ROUND(SUM(amount_usd), 2)`.
3. "How many different customers placed at least one paid order for a Rice Cooker?" -> `SELECT COUNT(DISTINCT customer) FROM orders WHERE LOWER(product) = 'rice cooker' AND status = 'paid';`
4. Always use `LOWER(product) = LOWER(...)` or `LOWER(region) = LOWER(...)`.
5. Return ONLY the raw SQL query inside ```sql ... ``` block.

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

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(sql)
        row = cursor.fetchone()
        conn.close()

        val = row[0] if row else None
        if isinstance(val, float):
            val = round(val, 2)

        return {"answer": val}

    except Exception as e:
        # Instead of 500, capture and surface the error detail for debugging
        return JSONResponse(
            status_code=200,
            content={"error": str(e), "traceback": traceback.format_exc()}
        )
