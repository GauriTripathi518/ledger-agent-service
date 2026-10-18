import os
import re
import json
import sqlite3
import requests
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

EXPORT_URL = "https://exam.sanand.workers.dev/questionData?email=23f2001555%40ds.study.iitm.ac.in&quizSign=xXlju3jeJXd3w91X4LtqY5Ga%2FbpFQO%2F0cJoPGCREt0l57XB17cMHkd0KlgA%2F8yUur1gz8jPfbQ2zDBQ5fIA%2FP5Pa97Ssx4BvH6QzNF53Q2iau9IrKEHA9%2BKQw4K2fOq9uUCeisT1ix297gT9D7xerumDgMVTVXAXd0aPUKFMKuHP0Q4goaoEdHcqnlUD3i0cvHFeAS1PosGnvNnjnAH2Afh1%2FE5JPge1vd0CdJP%2BYM27%2BMSCFjn9Gpbm%2Big%2FXmke%2BvazpT4p431lslg1Sut4aGipAdExbj0p7mSnyNP%2FdN3t6x0VEiVjdUovjP8ig7h%2FpreMLOZlwLacMD3rPoN%2FSOZ7q%2Bs9pDyzye9PQ%3D%3D&questionId=q-ledger-agent-server&path=%2Fexport"
RATES_URL = "https://exam.sanand.workers.dev/questionData?email=23f2001555%40ds.study.iitm.ac.in&quizSign=xXlju3jeJXd3w91X4LtqY5Ga%2FbpFQO%2F0cJoPGCREt0l57XB17cMHkd0KlgA%2F8yUur1gz8jPfbQ2zDBQ5fIA%2FP5Pa97Ssx4BvH6QzNF53Q2iau9IrKEHA9%2BKQw4K2fOq9uUCeisT1ix297gT9D7xerumDgMVTVXAXd0aPUKFMKuHP0Q4goaoEdHcqnlUD3i0cvHFeAS1PosGnvNnjnAH2Afh1%2FE5JPge1vd0CdJP%2BYM27%2BMSCFjn9Gpbm%2Big%2FXmke%2BvazpT4p431lslg1Sut4aGipAdExbj0p7mSnyNP%2FdN3t6x0VEiVjdUovjP8ig7h%2FpreMLOZlwLacMD3rPoN%2FSOZ7q%2Bs9pDyzye9PQ%3D%3D&questionId=q-ledger-agent-server&path=%2Frates"

DB_PATH = "/tmp/ledger.db"

def prepare_db():
    if os.path.exists(DB_PATH) and os.path.getsize(DB_PATH) > 1000:
        return

    rates = {"USD": 1.0, "EUR": 1.13, "INR": 0.01244}
    try:
        r_rates = requests.get(RATES_URL, timeout=8).json()
        rates.update(r_rates.get("usd_per_unit", {}))
    except Exception:
        pass

    resp = requests.get(EXPORT_URL, timeout=15)
    latest = {}
    for line in resp.iter_lines(decode_unicode=True):
        if not line or not line.strip():
            continue
        try:
            row = json.loads(line)
            oid = row.get("id")
            if not oid:
                continue
            if oid not in latest or str(row.get("updated_at", "")) > str(latest[oid].get("updated_at", "")):
                latest[oid] = row
        except Exception:
            continue

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
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

    rows = []
    for r in latest.values():
        curr = r.get("currency", "USD")
        rate = rates.get(curr, 1.0)
        amt = float(r.get("amount", 0.0))
        amt_usd = round(amt * rate, 2)

        ts = str(r.get("created_at", ""))
        y, m, dt = 2026, 1, ""
        if len(ts) >= 10:
            dt = ts[:10]
            try:
                parts = dt.split("-")
                y, m = int(parts[0]), int(parts[1])
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
            ts,
            str(r.get("updated_at", "")),
            amt_usd,
            dt,
            y,
            m
        ))

    cur.executemany("INSERT OR REPLACE INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()

def ask_gemini(question: str) -> str:
    api_key = os.getenv("GEMINI_API_KEY", "")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"

    schema_info = """
Table: orders
Columns:
- id TEXT
- customer TEXT (e.g. 'C1150')
- region TEXT ('Central', 'East', 'South', 'North')
- product TEXT ('Blender', 'Mixer', 'Toaster', 'Air Fryer', 'Rice Cooker', etc.)
- qty INTEGER
- unit_price REAL
- amount REAL
- currency TEXT
- status TEXT ('paid', 'void')
- created_at TEXT
- updated_at TEXT
- amount_usd REAL (value converted to USD)
- order_date TEXT (YYYY-MM-DD)
- order_year INTEGER
- order_month INTEGER (1=Jan, 2=Feb, ..., 6=Jun, etc.)

Rules:
1. "revenue" or "paid order" requires: status = 'paid'.
2. If asked total money/revenue in USD: ROUND(SUM(amount_usd), 2).
3. If asked "How many different customers placed at least one paid order for a Rice Cooker?":
   SELECT COUNT(DISTINCT customer) FROM orders WHERE LOWER(product) = 'rice cooker' AND status = 'paid';
4. If asked "Which product...": SELECT product FROM orders ...
5. Output ONLY a single SQL query inside ```sql ... ``` block. No markdown or explanation.
"""

    payload = {
        "contents": [{
            "parts": [{
                "text": f"{schema_info}\n\nQuestion: {question}"
            }]
        }]
    }

    resp = requests.post(url, json=payload, timeout=8)
    res_json = resp.json()

    candidates = res_json.get("candidates", [])
    if candidates:
        text = candidates[0]["content"]["parts"][0]["text"]
        match = re.search(r"```(?:sql)?(.*?)```", text, re.DOTALL)
        if match:
            return match.group(1).strip()
        return text.strip()
    return ""

@app.post("/answer")
async def answer(request: Request):
    try:
        body = await request.json()
        question = body.get("question", "")

        prepare_db()
        sql = ask_gemini(question)

        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(sql)
        row = cur.fetchone()
        conn.close()

        val = row[0] if row else None
        if isinstance(val, float):
            val = round(val, 2)

        return {"answer": val}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})
