import streamlit as st
import pandas as pd
from datetime import date, timedelta
import calendar
import json
import base64
from pathlib import Path
import altair as alt
from supabase import create_client

st.set_page_config(page_title="Meu Financeiro Online",page_icon="💰",layout="wide",initial_sidebar_state="expanded")

st.markdown("""
<style>
:root{color-scheme:light}
.stApp{background:#F3F6FB!important;color:#172033!important}
.block-container{padding-top:1.4rem;max-width:1400px}
h1,h2,h3,p,label,span{color:#172033}
[data-testid="stSidebar"]{background:#10192E!important}
[data-testid="stSidebar"] *{color:#F7F9FC!important}
[data-testid="stMetric"]{background:white;border:1px solid #E7EBF2;padding:18px;border-radius:18px;box-shadow:0 4px 18px #15223b0d}
[data-testid="stMetricLabel"] *{color:#687386!important}
[data-testid="stMetricValue"]{color:#10192E!important}
.stAlert{border-radius:14px}
div.stButton>button,div[data-testid="stFormSubmitButton"] button{
border-radius:12px;font-weight:700;background:#FFFFFF!important;color:#172033!important;border:1px solid #DCE3EE!important
}
div.stButton>button:hover,div[data-testid="stFormSubmitButton"] button:hover{
background:#EEF4FF!important;color:#10192E!important;border-color:#B8C8E3!important
}

/* Botões do menu lateral: contraste correto no fundo escuro */
[data-testid="stSidebar"] div.stButton>button{
background:#18243D!important;color:#F7F9FC!important;border:1px solid #34435F!important
}
[data-testid="stSidebar"] div.stButton>button *{color:#F7F9FC!important}
[data-testid="stSidebar"] div.stButton>button:hover{
background:#223252!important;color:#FFFFFF!important;border-color:#526481!important
}
[data-testid="stSidebar"] div.stButton>button:hover *{color:#FFFFFF!important}
</style>
""",unsafe_allow_html=True)

try:
    sb=create_client(st.secrets["SUPABASE_URL"],st.secrets["SUPABASE_KEY"])
except Exception:
    st.error("O banco online ainda não foi configurado. Siga o GUIA_PUBLICAR.txt.")
    st.stop()

def money(v):
    return f"R$ {float(v or 0):,.2f}".replace(",","X").replace(".",",").replace("X",".")

def fifth(y,m):
    d=date(y,m,1); n=0
    while True:
        if d.weekday()<5:
            n+=1
            if n==5:return d
        d+=timedelta(days=1)

def nextpay(d2,r1,r2):
    t=date.today(); a=fifth(t.year,t.month); b=date(t.year,t.month,min(max(int(d2),1),28)); opts=[]
    if a>=t:opts.append((a,r1))
    if b>=t:opts.append((b,r2))
    if not opts:
        y=t.year+(1 if t.month==12 else 0);m=1 if t.month==12 else t.month+1
        opts=[(fifth(y,m),r1)]
    return min(opts,key=lambda x:x[0])

def rows(table, order=None):
    q=sb.table(table).select("*")
    if order:q=q.order(order,desc=True)
    return q.execute().data or []

def myrows(table, order=None):
    q=sb.table(table).select("*").eq("user_id",st.session_state.uid)
    if order:q=q.order(order,desc=True)
    return q.execute().data or []

def recurring_summary():
    """Retorna recorrentes ativos, total mensal e valor ainda não marcado como pago no mês atual."""
    try:
        itens=myrows("recorrentes","dia_vencimento")
    except Exception:
        return [],0.0,0.0
    mes=date.today().strftime("%Y-%m")
    ativos=[x for x in itens if bool(x.get("ativo",True))]
    total=sum(float(x.get("valor") or 0) for x in ativos)
    pendente=sum(float(x.get("valor") or 0) for x in ativos if x.get("ultimo_pago_mes")!=mes)
    return ativos,total,pendente


def sync_recurring_payments():
    """Mantém o status mensal do recorrente e a movimentação real sincronizados, sem duplicar gastos."""
    mes=date.today().strftime("%Y-%m")
    hoje=date.today().isoformat()
