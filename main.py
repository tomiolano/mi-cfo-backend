import os
from datetime import date, datetime
from typing import List
import json

from fastapi import FastAPI, UploadFile, File, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker, Session
import pdfplumber
from google import genai

# Configuración de Base de Datos SQLite
SQLALCHEMY_DATABASE_URL = "sqlite:///./cfo_database.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- MODELOS DE BASE DE DATOS ---
class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True, index=True)
    description = Column(String, index=True)
    amount = Column(Float)
    type = Column(String)
    date = Column(Date, default=date.today)
    category = Column(String, index=True)

class RecurringBill(Base):
    __tablename__ = "recurring_bills"
    id = Column(Integer, primary_key=True, index=True)
    provider = Column(String, index=True)
    amount = Column(Float)
    due_date = Column(Date)
    category = Column(String)
    is_paid = Column(Boolean, default=False)

Base.metadata.create_all(bind=engine)

# --- ESQUEMAS DE FASTAPI ---
class PurchaseSimulation(BaseModel):
    item_name: str
    cost: float

app = FastAPI(title="Personal CFO API")

# Habilitar CORS para conectarse con Lovable.app
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/")
def read_root():
    return {"message": "El backend de tu CFO Personal está vivo en la nube!"}

@app.get("/dashboard/liquidity")
def get_liquidity(db: Session = Depends(get_db)):
    incomes = db.query(Transaction).filter(Transaction.type == "income").all()
    total_income = sum(t.amount for t in incomes)
    
    expenses = db.query(Transaction).filter(Transaction.type == "expense").all()
    total_expense = sum(t.amount for t in expenses)
    
    paid_bills = db.query(RecurringBill).filter(RecurringBill.is_paid == True).all()
    total_paid_bills = sum(b.amount for b in paid_bills)
    
    return {"current_liquidity": total_income - total_expense - total_paid_bills}

@app.post("/upload-invoice/")
async def upload_invoice(file: UploadFile = File(...), db: Session = Depends(get_db)):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="Falta API Key")
    
    content = await file.read()
    temp_pdf = f"temp_{file.filename}"
    with open(temp_pdf, "wb") as f: f.write(content)
        
    text = ""
    with pdfplumber.open(temp_pdf) as pdf:
        for page in pdf.pages: text += page.extract_text() + "\n"
    os.remove(temp_pdf)
    
    client = genai.Client(api_key=api_key)
    prompt = f'Extrae de la factura: Proveedor, Fecha de Vencimiento (YYYY-MM-DD), Monto Total (numero), Categoría. Devuelve SOLO un JSON valido con claves: "provider", "due_date", "total_amount", "category". Factura:\n{text}'
    
    response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
    data = json.loads(response.text.replace("```json", "").replace("```", "").strip())
    
    provider = data.get("provider")
    amount = float(data.get("total_amount", 0))
    
    alert = None
    prev = db.query(RecurringBill).filter(RecurringBill.provider == provider).order_by(RecurringBill.due_date.desc()).first()
    if prev:
        inc = ((amount - prev.amount) / prev.amount) * 100
        if inc > 5: alert = f"¡Alerta! {provider} aumentó un {inc:.1f}%"
            
    new_bill = RecurringBill(
        provider=provider, amount=amount, 
        due_date=datetime.strptime(data.get("due_date", "2099-01-01"), "%Y-%m-%d").date(),
        category=data.get("category")
    )
    db.add(new_bill)
    db.commit()
    
    return {"extracted_data": data, "alert": alert}
