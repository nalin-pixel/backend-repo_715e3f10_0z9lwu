import os
from datetime import date, datetime
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, validator

# Supabase client
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_KEY")

supabase_client = None
try:
    if SUPABASE_URL and SUPABASE_KEY:
        from supabase import create_client, Client  # type: ignore
        supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception:
    supabase_client = None

app = FastAPI(title="Meal Receipts Tracker API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -------------------- Models --------------------
class ReceiptIn(BaseModel):
    date: date
    meal_type: str = Field(pattern=r"^(lunch|dinner)$")
    amount: float = Field(gt=0)
    merchant: Optional[str] = None
    note: Optional[str] = None
    image_url: Optional[str] = None

    @validator("meal_type")
    def normalize_meal_type(cls, v):
        v = v.lower()
        if v not in ("lunch", "dinner"):
            raise ValueError("meal_type must be 'lunch' or 'dinner'")
        return v


class Receipt(ReceiptIn):
    id: int
    created_at: Optional[str] = None


class AdvanceIn(BaseModel):
    date: date
    amount: float = Field(gt=0)
    note: Optional[str] = None


class Advance(AdvanceIn):
    id: int
    created_at: Optional[str] = None


# -------------------- Helpers --------------------

def require_supabase():
    if not supabase_client:
        raise HTTPException(status_code=500, detail="Supabase not configured. Set SUPABASE_URL and SUPABASE_KEY environment variables.")
    return supabase_client


def parse_month(month: Optional[str]) -> tuple[date, date]:
    """Return first_day, last_day from 'YYYY-MM' or current month if None"""
    if not month:
        today = datetime.utcnow().date()
        start = today.replace(day=1)
    else:
        try:
            start = datetime.strptime(month, "%Y-%m").date().replace(day=1)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid month format. Use YYYY-MM")
    # compute end of month
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1, day=1)
    else:
        end = start.replace(month=start.month + 1, day=1)
    return start, end


# -------------------- Routes --------------------
@app.get("/")
def root():
    return {"message": "Meal Receipts Tracker API running"}


@app.get("/test")
def test():
    return {
        "backend": "✅ Running",
        "supabase_url": "✅ Set" if SUPABASE_URL else "❌ Not Set",
        "supabase_key": "✅ Set" if SUPABASE_KEY else "❌ Not Set",
        "connected": "✅" if supabase_client else "❌",
    }


@app.post("/api/receipt", response_model=Receipt)
def create_receipt(payload: ReceiptIn):
    sb = require_supabase()
    data = payload.model_dump()
    # Supabase stores dates as text/date; ensure ISO
    data["date"] = payload.date.isoformat()
    result = sb.table("receipts").insert(data).execute()
    if result.data is None or len(result.data) == 0:
        raise HTTPException(status_code=500, detail="Failed to insert receipt")
    return result.data[0]


@app.get("/api/receipts", response_model=List[Receipt])
def list_receipts(month: Optional[str] = Query(None, description="YYYY-MM")):
    sb = require_supabase()
    start, end = parse_month(month)
    result = (
        sb.table("receipts")
        .select("*")
        .gte("date", start.isoformat())
        .lt("date", end.isoformat())
        .order("date", desc=False)
        .execute()
    )
    return result.data or []


@app.post("/api/advance", response_model=Advance)
def create_advance(payload: AdvanceIn):
    sb = require_supabase()
    data = payload.model_dump()
    data["date"] = payload.date.isoformat()
    result = sb.table("advances").insert(data).execute()
    if result.data is None or len(result.data) == 0:
        raise HTTPException(status_code=500, detail="Failed to insert advance")
    return result.data[0]


@app.get("/api/advances", response_model=List[Advance])
def list_advances(month: Optional[str] = Query(None, description="YYYY-MM")):
    sb = require_supabase()
    start, end = parse_month(month)
    result = (
        sb.table("advances")
        .select("*")
        .gte("date", start.isoformat())
        .lt("date", end.isoformat())
        .order("date", desc=False)
        .execute()
    )
    return result.data or []


@app.get("/api/summary")
def monthly_summary(month: Optional[str] = Query(None, description="YYYY-MM")):
    sb = require_supabase()
    start, end = parse_month(month)

    r = (
        sb.rpc(
            "sum_amounts",
            {
                "table_name": "receipts",
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            },
        ).execute()
    )
    a = (
        sb.rpc(
            "sum_amounts",
            {
                "table_name": "advances",
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            },
        ).execute()
    )
    # Fallback if RPC not installed
    receipts_sum = 0.0
    advances_sum = 0.0
    if r.data is not None and isinstance(r.data, list) and len(r.data) > 0 and "sum" in r.data[0]:
        receipts_sum = float(r.data[0]["sum"] or 0)
    else:
        r2 = (
            sb.table("receipts")
            .select("amount")
            .gte("date", start.isoformat())
            .lt("date", end.isoformat())
            .execute()
        )
        receipts_sum = sum(float(x["amount"]) for x in (r2.data or []))

    if a.data is not None and isinstance(a.data, list) and len(a.data) > 0 and "sum" in a.data[0]:
        advances_sum = float(a.data[0]["sum"] or 0)
    else:
        a2 = (
            sb.table("advances")
            .select("amount")
            .gte("date", start.isoformat())
            .lt("date", end.isoformat())
            .execute()
        )
        advances_sum = sum(float(x["amount"]) for x in (a2.data or []))

    # breakdown lunch/dinner
    r_lunch = (
        sb.table("receipts")
        .select("amount")
        .eq("meal_type", "lunch")
        .gte("date", start.isoformat())
        .lt("date", end.isoformat())
        .execute()
    )
    r_dinner = (
        sb.table("receipts")
        .select("amount")
        .eq("meal_type", "dinner")
        .gte("date", start.isoformat())
        .lt("date", end.isoformat())
        .execute()
    )

    lunch_total = sum(float(x["amount"]) for x in (r_lunch.data or []))
    dinner_total = sum(float(x["amount"]) for x in (r_dinner.data or []))

    return {
        "month": start.strftime("%Y-%m"),
        "receipts_total": round(receipts_sum, 2),
        "advances_total": round(advances_sum, 2),
        "lunch_total": round(lunch_total, 2),
        "dinner_total": round(dinner_total, 2),
        "net": round(receipts_sum - advances_sum, 2),
    }


@app.get("/api/export.csv")
def export_csv(month: Optional[str] = Query(None, description="YYYY-MM")):
    sb = require_supabase()
    start, end = parse_month(month)

    r = (
        sb.table("receipts")
        .select("date,meal_type,amount,merchant,note")
        .gte("date", start.isoformat())
        .lt("date", end.isoformat())
        .order("date")
        .execute()
    ).data or []

    a = (
        sb.table("advances")
        .select("date,amount,note")
        .gte("date", start.isoformat())
        .lt("date", end.isoformat())
        .order("date")
        .execute()
    ).data or []

    # build CSV
    lines = ["type,date,meal,amount,merchant,note"]
    for row in r:
        lines.append(
            f"receipt,{row.get('date','')},{row.get('meal_type','')},{row.get('amount','')},{row.get('merchant','')},{row.get('note','')}"
        )
    for row in a:
        lines.append(
            f"advance,{row.get('date','')},,{row.get('amount','')},,{row.get('note','')}"
        )

    csv_text = "\n".join(lines) + "\n"
    return Response(content=csv_text, media_type="text/csv")


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
