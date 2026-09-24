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
    try:
        itens=myrows("recorrentes","dia_vencimento")
        movs=myrows("mov","data")
    except Exception:
        return
    markers={str(x.get("obs") or "") for x in movs}
    for x in itens:
        if not bool(x.get("ativo",True)):
            continue
        marker=f"RECORRENTE_AUTO:{x['id']}:{mes}"
        pago=x.get("ultimo_pago_mes")==mes
        existe=marker in markers
        if pago and not existe:
            sb.table("mov").insert({
                "user_id":st.session_state.uid,
                "data":hoje,
                "descricao":str(x.get("nome") or "Gasto recorrente"),
                "categoria":str(x.get("categoria") or "Outros"),
                "tipo":"Saída",
                "forma":str(x.get("forma") or "Outros"),
                "valor":float(x.get("valor") or 0),
                "obs":marker
            }).execute()
            markers.add(marker)
        elif (not pago) and existe:
            sb.table("mov").delete().eq("user_id",st.session_state.uid).eq("obs",marker).execute()
            markers.discard(marker)

def installment_summary():
