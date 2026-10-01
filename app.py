import streamlit as st
import pandas as pd
from datetime import date, timedelta
import calendar
import re
import unicodedata
import json
import secrets
import base64
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
import altair as alt
from supabase import create_client

st.set_page_config(page_title="Meu Financeiro Online",page_icon="💰",layout="wide",initial_sidebar_state="expanded")



try:
    sb=create_client(st.secrets["SUPABASE_URL"],st.secrets["SUPABASE_KEY"])
except Exception:
    st.error("O banco online ainda não foi configurado. Siga o GUIA_PUBLICAR.txt.")
    st.stop()


PLUGGY_API="https://api.pluggy.ai"

def pluggy_configured():
    try: return bool(st.secrets["PLUGGY_CLIENT_ID"]) and bool(st.secrets["PLUGGY_CLIENT_SECRET"])
    except Exception: return False

def pluggy_request(path, method="GET", body=None, api_key=None, timeout=20):
    data=None if body is None else json.dumps(body).encode("utf-8")
    headers={"Accept":"application/json"}
    if body is not None: headers["Content-Type"]="application/json"
    if api_key: headers["X-API-KEY"]=api_key
    req=urllib.request.Request(PLUGGY_API+path,data=data,headers=headers,method=method)
    try:
        with urllib.request.urlopen(req,timeout=timeout) as resp:
            raw=resp.read().decode("utf-8"); return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        try: detail=e.read().decode("utf-8")
        except Exception: detail=""
        raise RuntimeError(f"Pluggy HTTP {e.code}: {detail[:300]}")
    except Exception as e: raise RuntimeError(f"Não foi possível acessar a Pluggy: {e}")

def pluggy_api_key():
    if not pluggy_configured(): raise RuntimeError("Credenciais da Pluggy não configuradas nos Secrets.")
    return pluggy_request("/auth","POST",{"clientId":st.secrets["PLUGGY_CLIENT_ID"],"clientSecret":st.secrets["PLUGGY_CLIENT_SECRET"]}).get("apiKey")

def pluggy_connect_token():
    key=pluggy_api_key()
    token=pluggy_request("/connect_token","POST",{"options":{"clientUserId":str(st.session_state.uid),"avoidDuplicates":True}},key).get("accessToken")
    if not token: raise RuntimeError("A Pluggy não retornou o Connect Token.")
    return token

def pluggy_sandbox_connectors():
    """Lista conectores Sandbox disponíveis para esta aplicação Pluggy."""
    key=pluggy_api_key()
    data=pluggy_request("/connectors?"+urllib.parse.urlencode({"sandbox":"true"}),api_key=key)
    rows=data.get("results") or data.get("connectors") or []
    if isinstance(data,list): rows=data
    return rows

def pluggy_bank_sandbox():
    rows=pluggy_sandbox_connectors()
    def name_of(x): return str(x.get("name") or x.get("institutionUrl") or "").strip()
    exact=[x for x in rows if name_of(x).lower()=="pluggy bank"]
    candidates=exact or [x for x in rows if "pluggy bank" in name_of(x).lower()]
    return (candidates[0] if candidates else None), rows

def pluggy_items_for_user():
    key=pluggy_api_key(); q=urllib.parse.urlencode({"clientUserId":str(st.session_state.uid)})
    return pluggy_request("/v2/items?"+q,api_key=key).get("results",[]),key

def pluggy_accounts(item_id, api_key):
    q=urllib.parse.urlencode({"itemId":item_id,"type":"BANK"})
    return pluggy_request("/accounts?"+q,api_key=api_key).get("results",[])

def pluggy_transactions(account_id, api_key, max_pages=8):
    path="/v2/transactions?"+urllib.parse.urlencode({"accountId":account_id});out=[];pages=0
    while path and pages<max_pages:
        page=pluggy_request(path,api_key=api_key);out.extend(page.get("results",[]));nxt=page.get("next")
        path="/v2/transactions"+nxt if nxt else None;pages+=1
    return out

def pluggy_category(raw):
    s=str(raw or "").lower()
    pairs=[(["food","restaurant","grocer","supermarket"],"Alimentação"),(["transport","uber","taxi","fuel"],"Transporte"),(["health","pharmacy","medical"],"Saúde"),(["entertainment","leisure"],"Lazer"),(["subscription","service"],"Assinaturas"),(["shopping","retail"],"Compras"),(["rent","housing","utility"],"Moradia")]
    for words,cat in pairs:
        if any(w in s for w in words): return cat
    return "Outros"


def sync_pluggy_item(item_id):
    """Sincroniza um Item conhecido diretamente, sem depender de GET /v2/items."""
    key=pluggy_api_key()
    item=pluggy_request(f"/items/{item_id}",api_key=key)
    connector=item.get("connector") or {}
    bank_name=str(connector.get("name") or "Banco conectado")
    existing_banks=myrows("bancos")
    existing_mov=myrows("mov")
    bank_by_acc={str(x.get("pluggy_account_id")):x for x in existing_banks if x.get("pluggy_account_id")}
    markers={str(x.get("obs") or "") for x in existing_mov}
    accounts_count=0;tx_count=0
    for acc in pluggy_accounts(str(item_id),key):
        acc_id=str(acc.get("id") or "")
        if not acc_id: continue
        accounts_count+=1
        bal=float(acc.get("balance") or 0)
        acc_name=str(acc.get("marketingName") or acc.get("name") or "Conta")
        payload={"titular":"Conta conectada","nome":bank_name,"apelido":acc_name,
                 "tipo_conta":"Conta digital","saldo":max(bal,0),"ativo":True,
                 "pluggy_item_id":str(item_id),"pluggy_account_id":acc_id,"origem":"pluggy"}
        if acc_id in bank_by_acc:
            sb.table("bancos").update(payload).eq("id",bank_by_acc[acc_id]["id"]).eq("user_id",st.session_state.uid).execute()
        else:
            payload["user_id"]=st.session_state.uid
            sb.table("bancos").insert(payload).execute()
        for tx in pluggy_transactions(acc_id,key):
            txid=str(tx.get("id") or "")
            marker=f"PLUGGY_TX:{txid}"
            if not txid or marker in markers: continue
            amount=float(tx.get("amount") or 0)
            if amount==0: continue
            raw_date=str(tx.get("date") or "")[:10] or date.today().isoformat()
            sb.table("mov").insert({
                "user_id":st.session_state.uid,"data":raw_date,
                "descricao":str(tx.get("description") or tx.get("descriptionRaw") or "Movimentação bancária")[:180],
                "categoria":pluggy_category(tx.get("category")),
                "tipo":"Entrada" if amount>0 else "Saída","forma":"Banco conectado",
                "valor":abs(amount),"obs":marker
            }).execute()
            markers.add(marker);tx_count+=1
    st.cache_data.clear()
    return accounts_count,tx_count

# Streamlit Components v2: sem iframe isolado. O token temporário é o único dado enviado ao frontend.
PLUGGY_COMPONENT_HTML = """
<div class="mf-pluggy">
  <button id="pluggy-open" type="button">🏦 Abrir Pluggy Bank de teste</button>
  <div id="pluggy-status">Conexão segura pronta para abrir.</div>
</div>
"""
PLUGGY_COMPONENT_CSS = """
.mf-pluggy{font-family:var(--st-font);padding:.25rem 0}
#pluggy-open{width:100%;padding:.8rem 1rem;border-radius:.55rem;border:1px solid var(--st-primary-color);
background:var(--st-primary-color);color:white;font-weight:700;cursor:pointer}
#pluggy-status{margin-top:.6rem;color:var(--st-text-color);font-size:.9rem;opacity:.85}
"""
PLUGGY_COMPONENT_JS = r"""
export default function(component) {
  const { parentElement, data, setTriggerValue } = component;
  const btn = parentElement.querySelector("#pluggy-open");
  const status = parentElement.querySelector("#pluggy-status");
  let alive = true;

  function loadPluggy() {
    return new Promise((resolve, reject) => {
      if (window.PluggyConnect) { resolve(); return; }
      const existing = document.querySelector('script[data-mf-pluggy="1"]');
      if (existing) {
        existing.addEventListener("load", resolve, {once:true});
        existing.addEventListener("error", reject, {once:true});
        return;
      }
      const s=document.createElement("script");
      s.src="https://cdn.pluggy.ai/pluggy-connect/v2.7.0/pluggy-connect.js";
      s.async=true; s.dataset.mfPluggy="1";
      s.onload=resolve; s.onerror=reject;
      document.head.appendChild(s);
    });
  }

  btn.onclick = async () => {
    btn.disabled=true; status.textContent="Carregando Pluggy Connect...";
    try {
      await loadPluggy();
      if (!alive || !window.PluggyConnect) throw new Error("SDK não disponível");
      const pc = new window.PluggyConnect({
        connectToken: data.connectToken,
        includeSandbox: true,
        selectedConnectorId: data.selectedConnectorId ? Number(data.selectedConnectorId) : undefined,
        language: "pt",
        onOpen: () => { status.textContent="Pluggy Connect aberto."; },
        onSuccess: (payload) => {
          const item = payload?.item || payload;
          const itemId = item?.id || payload?.itemId || "";
          status.textContent = itemId ? "Banco de teste conectado com sucesso." : "Conectado, aguardando identificação do Item.";
          if (itemId) setTriggerValue("connected", itemId);
        },
        onError: (error) => {
          status.textContent="A Pluggy informou um erro na conexão.";
          setTriggerValue("connect_error", error?.message || "Erro de conexão");
        },
        onClose: () => { btn.disabled=false; }
      });
      pc.init();
    } catch (e) {
      status.textContent="Não foi possível carregar o Pluggy Connect: "+(e?.message || e);
      setTriggerValue("connect_error", e?.message || "Falha ao carregar SDK");
      btn.disabled=false;
    }
  };
  return () => { alive=false; };
}
"""
try:
    pluggy_connect_component = st.components.v2.component(
        "mf_pluggy_connect",
        html=PLUGGY_COMPONENT_HTML,
        css=PLUGGY_COMPONENT_CSS,
        js=PLUGGY_COMPONENT_JS,
    )
except Exception:
    pluggy_connect_component = None

# Componente de ditado gratuito: usa o reconhecimento de voz disponível no navegador.
VOICE_COMPONENT_HTML = """
<div class="mf-voice"><button id="mf-voice-btn" type="button">🎙️ Falar lançamento</button><div id="mf-voice-status">Toque e fale normalmente.</div></div>
"""
VOICE_COMPONENT_CSS = """
.mf-voice{font-family:var(--st-font);padding:.15rem 0}.mf-voice button{width:100%;padding:.8rem;border-radius:.55rem;border:1px solid var(--st-primary-color);background:var(--st-primary-color);color:white;font-weight:700;cursor:pointer}.mf-voice div{margin-top:.45rem;font-size:.88rem;opacity:.8}
"""
VOICE_COMPONENT_JS = r"""
export default function(component) {
  const {parentElement,setStateValue}=component;
  const btn=parentElement.querySelector("#mf-voice-btn"), status=parentElement.querySelector("#mf-voice-status");
  const SR=window.SpeechRecognition || window.webkitSpeechRecognition;
  if(!SR){btn.disabled=true;status.textContent="Reconhecimento de voz não disponível neste navegador. Use o microfone do teclado ou digite.";return;}
  btn.onclick=()=>{
    const rec=new SR(); rec.lang="pt-BR"; rec.interimResults=false; rec.maxAlternatives=1;
    status.textContent="Ouvindo... fale agora."; btn.disabled=true;
    rec.onresult=(e)=>{const t=e.results?.[0]?.[0]?.transcript||"";status.textContent=t?"Entendi: "+t:"Não consegui entender.";if(t)setStateValue("transcript",t);};
    rec.onerror=(e)=>{status.textContent="Não consegui ouvir. Você pode tentar novamente ou digitar.";setStateValue("voice_error",e?.error||"erro");};
    rec.onend=()=>{btn.disabled=false;}; rec.start();
  };
}
"""
try:
    voice_input_component=st.components.v2.component("mf_voice_input",html=VOICE_COMPONENT_HTML,css=VOICE_COMPONENT_CSS,js=VOICE_COMPONENT_JS)
except Exception:
    voice_input_component=None

def sync_pluggy_data():
    items,key=pluggy_items_for_user();existing_banks=myrows("bancos");existing_mov=myrows("mov")
    bank_by_acc={str(x.get("pluggy_account_id")):x for x in existing_banks if x.get("pluggy_account_id")}
    markers={str(x.get("obs") or "") for x in existing_mov};accounts_count=0;tx_count=0
    for item in items:
        item_id=str(item.get("id") or "");connector=item.get("connector") or {};bank_name=str(connector.get("name") or "Banco conectado")
        for acc in pluggy_accounts(item_id,key):
            acc_id=str(acc.get("id") or "")
            if not acc_id: continue
            accounts_count+=1;bal=float(acc.get("balance") or 0);acc_name=str(acc.get("marketingName") or acc.get("name") or "Conta")
            payload={"titular":"Conta conectada","nome":bank_name,"apelido":acc_name,"tipo_conta":"Conta digital","saldo":max(bal,0),"ativo":True,"pluggy_item_id":item_id,"pluggy_account_id":acc_id,"origem":"pluggy"}
            if acc_id in bank_by_acc: sb.table("bancos").update(payload).eq("id",bank_by_acc[acc_id]["id"]).eq("user_id",st.session_state.uid).execute()
            else:
                payload["user_id"]=st.session_state.uid;sb.table("bancos").insert(payload).execute()
            for tx in pluggy_transactions(acc_id,key):
                txid=str(tx.get("id") or "");marker=f"PLUGGY_TX:{txid}"
                if not txid or marker in markers: continue
                amount=float(tx.get("amount") or 0)
                if amount==0: continue
                sb.table("mov").insert({"user_id":st.session_state.uid,"data":str(tx.get("date") or "")[:10] or date.today().isoformat(),"descricao":str(tx.get("description") or tx.get("descriptionRaw") or "Movimentação bancária")[:180],"categoria":pluggy_category(tx.get("category")),"tipo":"Entrada" if amount>0 else "Saída","forma":"Banco conectado","valor":abs(amount),"obs":marker}).execute()
                markers.add(marker);tx_count+=1
    st.cache_data.clear();return len(items),accounts_count,tx_count

def income_rows():
    """Rendas fixas/planejadas da família. Compatível com contas antigas."""
    try:
        return myrows("rendas","created_at")
    except Exception:
        return []

def ensure_legacy_incomes(cfg):
    """Migra os dois recebimentos antigos uma única vez quando a tabela rendas existir."""
    try:
        atuais=income_rows()
        if atuais: return atuais
        legacy=[]
        r1=float(cfg.get("rec1") or 0); r2=float(cfg.get("rec2") or 0)
        if r1>0:
            legacy.append({"user_id":st.session_state.uid,"titular":"Titular principal","descricao":"Renda principal","valor":r1,"dia_recebimento":5,"tipo":"Fixa","incluir_familiar":True,"ativo":True})
        if r2>0:
            legacy.append({"user_id":st.session_state.uid,"titular":"Titular principal","descricao":"Segunda renda","valor":r2,"dia_recebimento":int(cfg.get("dia2") or 20),"tipo":"Fixa","incluir_familiar":True,"ativo":True})
        if legacy:
            sb.table("rendas").insert(legacy).execute(); st.cache_data.clear()
            return income_rows()
    except Exception:
        pass
    return []

def family_income_total(cfg=None):
    itens=income_rows()
    if itens:
        return sum(float(x.get("valor") or 0) for x in itens if bool(x.get("ativo",True)) and bool(x.get("incluir_familiar",True)))
    if cfg is not None:
        return float(cfg.get("rec1") or 0)+float(cfg.get("rec2") or 0)
    return 0.0

def next_family_income(cfg=None):
    hoje=date.today(); itens=[x for x in income_rows() if bool(x.get("ativo",True)) and bool(x.get("incluir_familiar",True)) and float(x.get("valor") or 0)>0]
    candidatos=[]
    for x in itens:
        dia=min(max(int(x.get("dia_recebimento") or 1),1),28)
        d=date(hoje.year,hoje.month,dia)
        if d<hoje:
            y=hoje.year+(1 if hoje.month==12 else 0); m=1 if hoje.month==12 else hoje.month+1; d=date(y,m,dia)
        candidatos.append((d,float(x.get("valor") or 0),str(x.get("descricao") or "Renda")))
    if candidatos: return min(candidatos,key=lambda z:z[0])
    if cfg is not None:
        d,v=nextpay(cfg.get("dia2",20),cfg.get("rec1",0),cfg.get("rec2",0)); return d,float(v),"Próximo recebimento"
    return hoje,0.0,"Sem renda cadastrada"

def interpretar_lancamento(texto):
    """Interpreta comandos financeiros comuns localmente, sem API paga."""
    original=(texto or "").strip()
    norm=unicodedata.normalize("NFKD",original.lower())
    norm="".join(c for c in norm if not unicodedata.combining(c))
    norm=norm.replace(" "," ")

    # Aceita R$15, R$ 15, 15 reais, 15,50 e 1.250,90.
    # Primeiro aceita números inteiros de qualquer tamanho (1500, 12000),
    # além de valores brasileiros formatados (1.500, 1.500,50) e decimais.
    # A ordem evita que "1500" seja lido parcialmente como "150".
    m=re.search(r'(?<!\d)(?:r\$\s*)?(\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+[.,]\d{1,2}|\d+)(?!\d)\s*(?:reais?|real)?',norm)
    valor=0.0
    if m:
        raw=m.group(1)
        if ',' in raw:
            raw=raw.replace('.','').replace(',','.')
        elif raw.count('.')>1 or ('.' in raw and len(raw.rsplit('.',1)[1])==3):
            raw=raw.replace('.','')
        try: valor=float(raw)
        except Exception: valor=0.0

    entrada=any(w in norm for w in ["recebi","receber","ganhei","entrou","entrada","vendi","faturei","caiu","depositaram","me pagaram"])
    saida=any(w in norm for w in ["gastei","paguei","comprei","saiu","saida","custou","abasteci","coloquei gasolina"])
    tipo="Entrada" if entrada and not saida else "Saída"
    forma="Pix" if "pix" in norm else "Dinheiro" if any(w in norm for w in ["dinheiro","especie"]) else "Crédito" if any(w in norm for w in ["credito","cartao de credito"]) else "Débito" if any(w in norm for w in ["debito","cartao de debito"]) else "Transferência" if "transfer" in norm else "Outro"
    data_mov=date.today()-timedelta(days=1) if "ontem" in norm else date.today()

    if tipo=="Entrada":
        categoria="Renda variável" if any(w in norm for w in ["uber","99","servico","freela","venda","entrega","ifood","corrida","bico"]) else "Outros"
    elif (
        any(w in norm for w in ["agua","luz","energia","internet","aluguel","condominio","telefone","celular"])
        or re.search(r"\bgas\b", norm)
    ):
        categoria="Moradia"
    elif any(w in norm for w in ["energetico","refrigerante","suco","cafe","lanche","salgado","coxinha","pastel","pao de queijo","hamburguer","pizza","comida","almoco","jantar","padaria","restaurante","ifood","mercado","supermercado"]):
        categoria="Alimentação"
    elif any(w in norm for w in ["gasolina","etanol","combustivel","posto","uber","99","taxi","pedagio"]):
        categoria="Transporte"
    else:
        categoria=sugerir_categoria(original)

    desc=original
    desc=re.sub(r'(?i)\b(recebi|receber|ganhei|gastei|paguei|comprei|caiu|entrou|vendi|faturei|reais?|real|no pix|via pix|em dinheiro|no cart[aã]o|de cr[eé]dito|de d[eé]bito|ontem|hoje)\b',' ',desc)
    desc=re.sub(r'(?i)r\$\s*',' ',desc)
    desc=re.sub(r'\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?|\d+(?:[\.,]\d{1,2})?',' ',desc)
    desc=re.sub(r'\b(com|de|do|da|no|na|em)\b',' ',desc,flags=re.I)
    desc=" ".join(desc.split()).strip(' -,.')
    if desc: desc=desc[:1].upper()+desc[1:]
    if not desc: desc="Recebimento" if tipo=="Entrada" else "Gasto"

    # Aprendizado local: se o usuário já corrigiu/salvou esta mesma descrição,
    # reutiliza a categoria do histórico. Entradas variáveis mantêm sua classificação própria.
    try:
        aprendida=categoria_aprendida(desc)
        # Só reutiliza categoria aprendida quando as regras atuais não reconheceram
        # uma categoria específica. Isso impede um histórico antigo incorreto de
        # transformar, por exemplo, Gasolina (Transporte) em Moradia.
        if aprendida and categoria=="Outros":
            categoria=aprendida
    except Exception:
        pass

    return {"texto":original,"tipo":tipo,"valor":valor,"descricao":desc[:180],"categoria":categoria,"forma":forma,"data":data_mov}

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
    """Parcelamentos ativos e valor realmente devido no mês atual."""
    try:
        itens=myrows("parcelamentos","dia_vencimento")
    except Exception:
        return [],0.0
    hoje=date.today(); mes=hoje.strftime("%Y-%m")
    fim_mes=date(hoje.year,hoje.month,calendar.monthrange(hoje.year,hoje.month)[1])
    ativos=[]; pendente=0.0
    for x in itens:
        if not bool(x.get("ativo",True)) or int(x.get("parcelas_pagas") or 0)>=int(x.get("total_parcelas") or 0): continue
        primeira=x.get("primeira_parcela")
        if primeira:
            try:
                if pd.to_datetime(primeira).date()>fim_mes:
                    continue
            except Exception: pass
        ativos.append(x)
        if x.get("ultimo_pago_mes")!=mes: pendente+=float(x.get("valor_parcela") or 0)
    return ativos,pendente

def sync_installment_payments():
    """Cria/remove o gasto da parcela do mês e mantém o contador de parcelas sincronizado."""
    mes=date.today().strftime("%Y-%m")
    hoje=date.today().isoformat()
    try:
        itens=myrows("parcelamentos","dia_vencimento")
        movs=myrows("mov","data")
    except Exception:
        return
    markers={str(x.get("obs") or "") for x in movs}
    for x in itens:
        marker=f"PARCELA_AUTO:{x['id']}:{mes}"
        pago=x.get("ultimo_pago_mes")==mes
        existe=marker in markers
        if pago and not existe:
            sb.table("mov").insert({
                "user_id":st.session_state.uid,"data":hoje,
                "descricao":str(x.get("nome") or "Compra parcelada"),
                "categoria":str(x.get("categoria") or "Compras"),
                "tipo":"Saída","forma":str(x.get("forma") or "Crédito"),
                "valor":float(x.get("valor_parcela") or 0),"obs":marker
            }).execute(); markers.add(marker)
        elif (not pago) and existe:
            sb.table("mov").delete().eq("user_id",st.session_state.uid).eq("obs",marker).execute(); markers.discard(marker)

def ensure_config():
    r=sb.table("config").select("*").eq("user_id",st.session_state.uid).execute().data
    if not r:
        sb.table("config").insert({
            "user_id":st.session_state.uid,
            "rec1":0,
            "rec2":0,
            "dia2":20,
            "pct":20,
            "reserva":0,
            "tutorial_oculto":False
        }).execute()
        r=sb.table("config").select("*").eq("user_id",st.session_state.uid).execute().data
    return r[0]

APP_URL = "https://meu-financeiro-2027.streamlit.app"

# Restaura a sessão autenticada do Supabase a cada rerun do Streamlit.

# Ajuste visual do tutorial: fundo claro e texto com alto contraste.
st.markdown("""
<style>
div[data-testid="stDialog"] div[role="dialog"] {
    background-color: #FFFFFF !important;
    color: #172033 !important;
}
div[data-testid="stDialog"] div[role="dialog"] h1,
div[data-testid="stDialog"] div[role="dialog"] h2,
div[data-testid="stDialog"] div[role="dialog"] h3,
div[data-testid="stDialog"] div[role="dialog"] p,
div[data-testid="stDialog"] div[role="dialog"] span,
div[data-testid="stDialog"] div[role="dialog"] label {
    color: #172033 !important;
}
div[data-testid="stDialog"] div[role="dialog"] [data-testid="stCaptionContainer"] p {
    color: #5B6475 !important;
}
div[data-testid="stDialog"] div[role="dialog"] [data-testid="stAlert"] {
    background-color: #EAF4FF !important;
    border: 1px solid #B8D9FF !important;
}
div[data-testid="stDialog"] div[role="dialog"] [data-testid="stAlert"] p,
div[data-testid="stDialog"] div[role="dialog"] [data-testid="stAlert"] span {
    color: #17324D !important;
}
div[data-testid="stDialog"] div[role="dialog"] button[kind="primary"] p {
    color: #FFFFFF !important;
}
</style>
""", unsafe_allow_html=True)

def restore_session():
    access_token = st.session_state.get("access_token")
    refresh_token = st.session_state.get("refresh_token")
    if access_token and refresh_token:
        try:
            session = sb.auth.set_session(access_token, refresh_token)
            if session and session.session:
                st.session_state.access_token = session.session.access_token
                st.session_state.refresh_token = session.session.refresh_token
            return True
        except Exception:
            for k in ["uid", "email", "access_token", "refresh_token"]:
                st.session_state.pop(k, None)
    return False


def save_auth_session(result):
    if not result or not result.user or not result.session:
        return False
    st.session_state.uid = result.user.id
    st.session_state.email = result.user.email
    st.session_state.access_token = result.session.access_token
    st.session_state.refresh_token = result.session.refresh_token
    return True


def handle_auth_callback():
    # Links de confirmação/recuperação do Supabase podem voltar com ?code=...
    code = st.query_params.get("code")
    if code and not st.session_state.get("access_token"):
        try:
            result = sb.auth.exchange_code_for_session({"auth_code": code})
            if save_auth_session(result):
                st.session_state.reset_mode = True
                st.query_params.clear()
                st.rerun()
        except Exception as e:
            st.error(f"Não foi possível validar o link recebido por e-mail: {e}")


def reset_password_screen():
    st.title("🔑 Criar nova senha")
    st.caption("Digite sua nova senha para concluir a recuperação da conta.")
    with st.form("new_password"):
        senha = st.text_input("Nova senha", type="password")
        senha2 = st.text_input("Repita a nova senha", type="password")
        if st.form_submit_button("Salvar nova senha", use_container_width=True):
            if len(senha) < 6:
                st.error("Use uma senha com pelo menos 6 caracteres.")
            elif senha != senha2:
                st.error("As senhas não são iguais.")
            else:
                try:
                    sb.auth.update_user({"password": senha})
                    st.session_state.reset_mode = False
                    st.success("Senha alterada com sucesso. Você já pode continuar usando sua conta.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Não foi possível alterar a senha: {e}")


def login():
    """V26 — login com vídeo de fundo, preservando widgets nativos."""
    video_path = Path(__file__).with_name("login_bg.mp4")
    video_b64 = ""
    if video_path.exists():
        try:
            video_b64 = base64.b64encode(video_path.read_bytes()).decode("utf-8")
        except Exception:
            video_b64 = ""

    if video_b64:
        video_html = (
            '<div id="mf-video-bg"><video autoplay muted loop playsinline preload="auto">'
            f'<source src="data:video/mp4;base64,{video_b64}" type="video/mp4">'
            '</video></div>'
        )
        st.markdown(video_html, unsafe_allow_html=True)

    st.markdown("""
    <style>
    [data-testid="stSidebar"]{display:none!important}
    [data-testid="stHeader"]{background:transparent!important}
    .stApp,[data-testid="stAppViewContainer"]{background:#07111f!important}
    #mf-video-bg{position:fixed;inset:0;width:100vw;height:100vh;z-index:0;overflow:hidden;pointer-events:none}
    #mf-video-bg video{width:100%;height:100%;object-fit:cover;object-position:center center;filter:brightness(1.03) saturate(1.04)}
    [data-testid="stAppViewContainer"]>.main{position:relative!important;z-index:2!important;background:transparent!important}
    [data-testid="stAppViewContainer"] .block-container{
        position:relative!important;z-index:3!important;width:460px!important;
        max-width:calc(100vw - 48px)!important;margin-left:auto!important;
        margin-right:5vw!important;padding-top:10vh!important;padding-bottom:3rem!important}
    [data-testid="stTabs"]{
        background:rgba(4,14,28,.76)!important;border:1px solid rgba(135,190,255,.32)!important;
        border-radius:22px!important;padding:20px!important;backdrop-filter:blur(14px)!important;
        -webkit-backdrop-filter:blur(14px)!important;box-shadow:0 22px 65px rgba(0,0,0,.38)!important}
    h1{color:#fff!important;text-shadow:0 2px 14px rgba(0,0,0,.72)!important}
    .mf-login-sub{color:#f2f7ff!important;text-shadow:0 2px 10px rgba(0,0,0,.72)!important;margin-top:-8px;margin-bottom:18px}
    [data-testid="stTabs"] p,[data-testid="stTabs"] label,[data-testid="stTabs"] span,[data-testid="stTabs"] button{color:#fff!important}
    [data-testid="stTabs"] [data-baseweb="input"]>div{background:rgba(4,13,26,.78)!important}
    @media(min-width:801px){
        [data-testid="stAppViewContainer"] .block-container{
            width:600px!important;
            max-width:600px!important;
            margin-left:auto!important;
            margin-right:auto!important;
            padding-top:8vh!important;
        }
        [data-testid="stTabs"]{padding:26px!important}
    }
    @media(max-width:800px){
        #mf-video-bg video{object-position:43% center;filter:brightness(1.12) saturate(1.04)}
        [data-testid="stAppViewContainer"] .block-container{
            width:calc(100vw - 28px)!important;max-width:none!important;margin:0 auto!important;padding-top:5vh!important}
        [data-testid="stTabs"]{background:rgba(4,14,28,.66)!important;padding:16px!important;
            backdrop-filter:blur(11px)!important;-webkit-backdrop-filter:blur(11px)!important}
    }
    </style>
    """, unsafe_allow_html=True)

    st.title("💰 Meu Financeiro")
    st.markdown('<div class="mf-login-sub">Seu dinheiro sob controle. Planeje, acompanhe e conquiste seus objetivos.</div>', unsafe_allow_html=True)
    tab1, tab2 = st.tabs(["Entrar", "Criar conta"])

    with tab1:
        with st.form("login"):
            email = st.text_input("E-mail", placeholder="Digite seu e-mail")
            senha = st.text_input("Senha", type="password", placeholder="Digite sua senha")
            entrar = st.form_submit_button("Entrar", use_container_width=True)
        if entrar:
            try:
                r = sb.auth.sign_in_with_password({"email": email, "password": senha})
                if save_auth_session(r):
                    st.rerun()
                else:
                    st.error("Não foi possível iniciar a sessão.")
            except Exception:
                st.error("E-mail ou senha inválidos, ou não foi possível conectar agora.")

        with st.expander("🔑 Esqueci minha senha"):
            recovery_email = st.text_input("E-mail da conta", key="recovery_email")
            if st.button("Enviar e-mail de recuperação", use_container_width=True):
                if not recovery_email.strip():
                    st.warning("Informe seu e-mail.")
                else:
                    try:
                        sb.auth.reset_password_for_email(recovery_email.strip(), {"redirect_to": APP_URL})
                        st.success("Solicitação enviada. Se o envio de e-mail estiver configurado no projeto, confira sua caixa de entrada.")
                    except Exception:
                        st.error("Não foi possível solicitar a recuperação agora.")

    with tab2:
        with st.form("signup"):
            email = st.text_input("Seu e-mail", key="se", placeholder="Digite seu e-mail")
            senha = st.text_input("Crie uma senha", type="password", key="ss")
            senha2 = st.text_input("Repita a senha", type="password")
            criar = st.form_submit_button("Criar minha conta", use_container_width=True)
        if criar:
            if len(senha) < 6:
                st.error("Use uma senha com pelo menos 6 caracteres.")
            elif senha != senha2:
                st.error("As senhas não são iguais.")
            else:
                try:
                    r = sb.auth.sign_up({"email": email, "password": senha, "options": {"email_redirect_to": APP_URL}})
                    if r.session:
                        save_auth_session(r)
                        st.success("Conta criada com sucesso.")
                        st.rerun()
                    else:
                        st.success("Conta criada. Se a confirmação por e-mail estiver ativada, confirme antes de entrar.")
                except Exception:
                    st.error("Não foi possível criar a conta agora.")



# V60 — uso diário em janelas rápidas, sem sair do painel.
@st.dialog("➕ Receber dinheiro")
def quick_income_dialog():
    st.caption("Registre uma entrada sem sair do painel.")
    with st.form("quick_income_form",clear_on_submit=True):
        desc=st.text_input("De onde veio?",placeholder="Ex.: Salário extra, venda, reembolso")
        valor=st.number_input("Quanto recebeu?",min_value=0.0,step=50.0,format="%.2f")
        data_mov=st.date_input("Quando recebeu?",value=date.today())
        forma=st.selectbox("Como recebeu?",["Pix","Dinheiro","Transferência","Outro"])
        if st.form_submit_button("✅ Registrar",use_container_width=True):
            if not desc.strip() or valor<=0: st.warning("Informe a origem e um valor maior que zero.")
            else:
                sb.table("mov").insert({"user_id":st.session_state.uid,"data":data_mov.isoformat(),"descricao":desc.strip(),"categoria":"Outros","tipo":"Entrada","forma":forma,"valor":float(valor),"obs":""}).execute()
                st.cache_data.clear(); st.session_state["_flash_inicio"]=f"✅ Recebimento de {money(valor)} registrado."; st.rerun()

@st.dialog("➖ Registrar gasto")
def quick_expense_dialog():
    st.caption("Registre um gasto sem sair do painel.")
    desc=st.text_input("Com o que gastou?",placeholder="Ex.: Mercado, Uber, farmácia",key="quick_expense_desc")
    categorias=["Moradia","Alimentação","Transporte","Saúde","Lazer","Assinaturas","Compras","Outros"]
    sugerida=sugerir_categoria(desc); idx=categorias.index(sugerida) if sugerida in categorias else len(categorias)-1
    if desc.strip(): st.caption(f"✨ Categoria sugerida: {sugerida}")
    with st.form("quick_expense_form",clear_on_submit=True):
        valor=st.number_input("Quanto gastou?",min_value=0.0,step=20.0,format="%.2f")
        a,b=st.columns(2); categoria=a.selectbox("Categoria",categorias,index=idx); forma=b.selectbox("Como pagou?",["Pix","Débito","Crédito","Dinheiro","Outro"])
        data_mov=st.date_input("Quando gastou?",value=date.today())
        if st.form_submit_button("✅ Registrar",use_container_width=True):
            if not desc.strip() or valor<=0: st.warning("Informe o gasto e um valor maior que zero.")
            else:
                sb.table("mov").insert({"user_id":st.session_state.uid,"data":data_mov.isoformat(),"descricao":desc.strip(),"categoria":categoria,"tipo":"Saída","forma":forma,"valor":float(valor),"obs":""}).execute()
                st.cache_data.clear(); st.session_state["_flash_inicio"]=f"✅ Gasto de {money(valor)} registrado."; st.rerun()

@st.dialog("🧾 Nova conta")
def quick_bill_dialog():
    st.caption("Adicione uma conta aos seus compromissos sem sair do painel.")
    with st.form("quick_bill_form",clear_on_submit=True):
        nome=st.text_input("Qual é a conta?",placeholder="Ex.: Energia, celular, escola")
        valor=st.number_input("Qual o valor?",min_value=0.0,step=20.0,format="%.2f")
        a,b=st.columns(2); venc=a.date_input("Quando vence?",value=date.today()); categoria=b.selectbox("Categoria",["Moradia","Saúde","Transporte","Assinaturas","Outros"])
        if st.form_submit_button("✅ Adicionar",use_container_width=True):
            if not nome.strip() or valor<=0: st.warning("Informe o nome e um valor maior que zero.")
            else:
                sb.table("contas").insert({"user_id":st.session_state.uid,"nome":nome.strip(),"categoria":categoria,"valor":float(valor),"vencimento":venc.isoformat(),"status":"Pendente"}).execute()
                st.cache_data.clear(); st.session_state["_flash_inicio"]=f"✅ Conta {nome.strip()} adicionada."; st.rerun()

@st.dialog("🎯 Definir orçamento")
def quick_budget_dialog():
    st.caption("Defina quanto pretende gastar por mês em uma categoria.")
    cats=["Moradia","Alimentação","Transporte","Saúde","Lazer","Assinaturas","Compras","Outros"]
    with st.form("quick_budget_form"):
        cat=st.selectbox("Categoria",cats)
        limite=st.number_input("Limite mensal",min_value=0.0,step=50.0,format="%.2f")
        if st.form_submit_button("💾 Salvar orçamento",use_container_width=True):
            if limite<=0: st.warning("Informe um limite maior que zero.")
            else:
                existente=sb.table("orcamentos").select("id").eq("user_id",st.session_state.uid).eq("categoria",cat).execute().data or []
                if existente:
                    sb.table("orcamentos").update({"limite":float(limite)}).eq("id",existente[0]["id"]).eq("user_id",st.session_state.uid).execute()
                else:
                    sb.table("orcamentos").insert({"user_id":st.session_state.uid,"categoria":cat,"limite":float(limite)}).execute()
                st.cache_data.clear(); st.session_state["_flash_inicio"]=f"✅ Orçamento de {cat} atualizado."; st.rerun()


@st.dialog("🎯 Atualizar meta")
def manage_goal_dialog(meta):
    nome=str(meta.get("nome") or "Meta"); atual=float(meta.get("guardado") or 0); desejado=float(meta.get("desejado") or 0)
    st.write(f"**{nome}**")
    st.caption(f"Guardado agora: {money(atual)} · Objetivo: {money(desejado)}")
    acao=st.radio("O que deseja fazer?",["Adicionar dinheiro","Retirar dinheiro","Editar meta","Excluir meta"])
    if acao in ["Adicionar dinheiro","Retirar dinheiro"]:
        valor=st.number_input("Valor",min_value=0.0,step=50.0,format="%.2f",key=f"goal_value_{meta['id']}")
        if st.button("💾 Confirmar",use_container_width=True,key=f"goal_confirm_{meta['id']}"):
            if valor<=0: st.warning("Informe um valor maior que zero.")
            else:
                novo=atual+valor if acao=="Adicionar dinheiro" else max(atual-valor,0)
                sb.table("metas").update({"guardado":novo}).eq("id",meta["id"]).eq("user_id",st.session_state.uid).execute()
                st.cache_data.clear();st.rerun()
    elif acao=="Editar meta":
        novo_nome=st.text_input("Nome",value=nome,key=f"goal_name_{meta['id']}")
        novo_desejado=st.number_input("Valor desejado",min_value=0.0,value=desejado,step=100.0,format="%.2f",key=f"goal_target_{meta['id']}")
        if st.button("💾 Salvar alterações",use_container_width=True,key=f"goal_edit_{meta['id']}"):
            if not novo_nome.strip() or novo_desejado<=0: st.warning("Informe nome e objetivo maior que zero.")
            else:
                sb.table("metas").update({"nome":novo_nome.strip(),"desejado":float(novo_desejado)}).eq("id",meta["id"]).eq("user_id",st.session_state.uid).execute()
                st.cache_data.clear();st.rerun()
    else:
        st.warning("A exclusão remove somente esta meta de acompanhamento.")
        if st.button("🗑️ Excluir definitivamente",use_container_width=True,key=f"goal_delete_{meta['id']}"):
            sb.table("metas").delete().eq("id",meta["id"]).eq("user_id",st.session_state.uid).execute()
            st.cache_data.clear();st.rerun()

@st.dialog("👋 Bem-vindo ao Meu Financeiro", width="large")
def tutorial_dialog():
    st.caption("Guia rápido para você saber onde começar e para que serve cada área.")

    st.markdown("### 1️⃣ Configure sua renda")
    st.write("Na tela **🏠 Início**, clique em **⚙️ Minha renda**. Você pode cadastrar quantas rendas e titulares quiser; o sistema soma somente as rendas ativas marcadas para a família. Também defina quanto quer guardar e sua reserva mínima.")

    st.markdown("### 2️⃣ Registre por voz ou texto")
    st.write("Em **🎙️ MEFI**, fale pelo microfone do teclado do celular ou digite frases como “Recebi 15 de Uber no Pix” e “Paguei 136 da água”. O sistema interpreta entrada/saída, valor, categoria e forma de pagamento e sempre mostra uma confirmação antes de salvar.")

    st.markdown("### 3️⃣ Converse com o MEFI — Assistente Inteligente")
    st.write("Em **🤖 MEFI**, faça perguntas sobre os seus próprios números, como quanto pode gastar, onde gastou mais e como está o mês. Os três botões rápidos sempre substituem a análise anterior, deixando a tela limpa; perguntas digitadas no chat continuam formando uma conversa. Os atalhos principais são calculados pelo próprio sistema e funcionam mesmo quando o serviço externo de IA está ocupado. Para perguntas livres, a IA usa somente um resumo dos valores financeiros necessários e, se o serviço estiver indisponível, o sistema responde com uma análise local.")

    st.markdown("### 4️⃣ Acompanhe sua Saúde Financeira")
    st.write("Em **🚦 Saúde Financeira**, veja quanto da sua renda está comprometida e acompanhe o indicador **Tranquilo, Atenção ou Orçamento apertado**.")

    st.markdown("### 5️⃣ Veja sua Previsão Financeira")
    st.write("Em **🔮 Previsão**, acompanhe uma estimativa do fim do mês com base no seu ritmo de gastos, contas pendentes e meta de economia. Você também pode comparar três cenários.")

    st.markdown("### 6️⃣ Cadastre gastos recorrentes")
    st.write("Em **🔁 Recorrentes**, cadastre compromissos que se repetem todos os meses, como internet, streaming, academia e mensalidades. Marque como pago no mês para evitar que o valor continue aparecendo como compromisso pendente.")

    st.markdown("### 7️⃣ Controle compras parceladas")
    st.write("Em **💳 Parcelamentos**, informe a compra, valor da parcela, total de parcelas e quantas já foram pagas. O sistema mostra o progresso, saldo restante, próxima parcela e inclui automaticamente a parcela pendente nos cálculos e vencimentos. Ao marcar a parcela do mês como paga, ela vira gasto realizado sem ser contada duas vezes.")

    st.markdown("### 8️⃣ Organize os bancos da família")
    st.write("Em **🏦 Bancos**, você pode cadastrar quantas contas quiser e indicar o **titular** de cada uma, por exemplo você e sua esposa. A Central mostra o saldo por titular e o saldo familiar, permite filtrar, editar e excluir contas. Por enquanto esses saldos ficam separados do cálculo principal da tela Início. A futura conexão Open Finance será feita por autorização segura e nunca pedirá sua senha bancária dentro do Meu Financeiro.")

    st.markdown("### 9️⃣ Organize suas contas")
    st.write("Em **🧾 Contas**, cadastre contas a pagar, vencimentos e marque cada uma como Pendente, Pago ou Atrasado.")

    st.markdown("### 🔟 Acompanhe seus cartões")
    st.write("Em **💳 Cartões**, informe limite, fatura atual, dia de fechamento e vencimento.")

    st.markdown("### 1️⃣1️⃣ Controle dívidas e objetivos")
    st.write("Use **📉 Dívidas** para acompanhar saldo e parcelas. Em **🎯 Metas**, registre quanto deseja juntar e quanto já guardou.")

    st.markdown("### 💬 MEFI no WhatsApp — preparação")
    st.write("A arquitetura do MEFI está sendo preparada para usar a mesma conta no site, aplicativo e futuramente no WhatsApp. O telefone será vinculado com autorização à conta do usuário; receber uma mensagem de um número, sozinho, não dará acesso aos dados financeiros. Mais de um número poderá ser autorizado em uma conta familiar no futuro.")

    st.markdown("### 🧭 Menu simplificado")
    st.write("O menu principal agora mostra apenas as áreas essenciais. As funções de Registrar, Contas, Recorrentes, Parcelamentos, Cartões, Previsão e Relatórios continuam preservadas no sistema enquanto são reorganizadas para um uso mais simples pela tela **🏠 Início**.")

    st.markdown("### 👋 Primeiros passos")
    st.write("Quando uma conta ainda não está configurada, o **Início** mostra um guia de 3 passos: **Configure sua renda**, **Adicione seu banco** e **Adicione um compromisso**. Cada passo recebe um ✅ automaticamente quando é concluído. Depois dos três, o guia desaparece sozinho para não ocupar espaço.")

    st.markdown("### ✨ Modo Simples")
    st.write("Na tela **🏠 Início**, os atalhos **Recebi dinheiro**, **Gastei dinheiro** e **Tenho uma conta** abrem telas simples e dedicadas, com apenas as informações necessárias e uma confirmação clara depois de salvar. Em **Posso comprar?**, informe o valor e escolha à vista ou parcelado para ver o impacto antes da compra. Em **📂 Meus compromissos**, a seção **Para pagar agora** mostra contas, gastos mensais e parcelas pendentes com o botão **Paguei**. Depois que uma parcela é paga, ela aparece em **Próximos compromissos** quando ainda houver parcelas futuras, sem permitir pagamento antecipado por engano. Na última parcela, o parcelamento é concluído automaticamente.")

    st.markdown("### 1️⃣2️⃣ Entenda a tela Início")
    st.write("O **🏠 Início** funciona como um painel único. Em **⚡ Ações rápidas**, **Receber**, **Gastar** e **Nova conta** abrem uma janela sobre o painel. As movimentações podem ser buscadas por nome ou categoria, o gráfico mostra para onde foi o dinheiro e **🎯 Orçamento por categoria** compara o gasto do mês com os limites que você definiu. Em **📅 Contas e compromissos**, os vencimentos continuam organizados por urgência e o botão **Paguei** registra o pagamento.")

    st.markdown("### 1️⃣3️⃣ Consulte seus relatórios")
    st.write("Em **📊 Relatórios**, veja seus lançamentos e a distribuição dos gastos por categoria.")

    st.markdown("### 📱 Vincule o WhatsApp ao MEFI")
    st.write("Em **📱 WhatsApp**, cadastre os celulares autorizados para a sua conta. Nesta etapa o número fica **pré-vinculado** ao seu usuário e recebe um código de confirmação. A verificação pelo próprio WhatsApp será concluída quando o canal oficial do MEFI for ativado. Você pode ter mais de um número autorizado e desvincular qualquer um deles.")

    st.info("💡 Você pode abrir este tutorial novamente a qualquer momento pelo botão **❓ Tutorial / Ajuda** no menu lateral.")

    nao_mostrar = st.checkbox(
        "Não mostrar este tutorial automaticamente novamente",
        value=False,
        key="tutorial_nao_mostrar"
    )

    if st.button("✅ Entendi, começar", use_container_width=True, type="primary"):
        if nao_mostrar:
            try:
                sb.table("config").update(
                    {"tutorial_oculto": True}
                ).eq("user_id", st.session_state.uid).execute()
                st.session_state.tutorial_oculto = True
            except Exception as e:
                st.error(f"Não foi possível salvar sua preferência: {e}")
                return
        st.session_state.tutorial_mostrado_sessao = True
        st.rerun()


handle_auth_callback()

if st.session_state.get("access_token"):
    restore_session()

if st.session_state.get("reset_mode"):
    reset_password_screen()
    st.stop()

if "uid" not in st.session_state or not st.session_state.get("access_token"):
    login()
    st.stop()

cfg=ensure_config()
ensure_legacy_incomes(cfg)
# V8: transforma recorrentes marcados como pagos em gastos realizados e reconcilia dados antigos.
sync_recurring_payments()
sync_installment_payments()
st.session_state.tutorial_oculto = bool(cfg.get("tutorial_oculto", False))

st.sidebar.title("💰 Meu Financeiro")
st.sidebar.caption(st.session_state.get("email",""))

def normalizar_descricao(descricao):
    import unicodedata, re
    texto=unicodedata.normalize("NFKD", (descricao or "").lower().strip())
    texto="".join(c for c in texto if not unicodedata.combining(c))
    texto=re.sub(r"[^a-z0-9 ]+", " ", texto)
    return " ".join(texto.split())

def categoria_aprendida(descricao):
    alvo=normalizar_descricao(descricao)
    if not alvo:
        return None
    historico=myrows("mov","data")
    candidatos=[]
    for m in historico:
        if normalizar_descricao(m.get("descricao",""))==alvo and m.get("categoria"):
            candidatos.append(m.get("categoria"))
    return candidatos[-1] if candidatos else None

def sugerir_categoria(descricao):
    texto=(descricao or "").lower().strip()
    regras={
        "Transporte":["uber","99","taxi","táxi","posto","gasolina","etanol","combustivel","combustível","pedagio","pedágio","estacionamento"],
        "Alimentação":["ifood","restaurante","lanche","salgado","coxinha","pastel","pão de queijo","pao de queijo","pizza","hamburguer","hambúrguer","padaria","cafe","café","almoço","almoco","jantar","mercado","supermercado","energetico","energético","refrigerante","suco","bebida"],
        "Assinaturas":["netflix","spotify","disney","prime video","amazon prime","youtube premium","hbo","max","deezer","icloud","google one"],
        "Saúde":["farmacia","farmácia","drogaria","medico","médico","consulta","dentista","hospital","exame"],
        "Moradia":["aluguel","condominio","condomínio","energia","luz","agua","água","internet","iptu"],
        "Compras":["shopee","mercado livre","amazon","magalu","magazine luiza","roupa","calçado","calcado"],
        "Lazer":["cinema","show","bar","viagem","hotel","ingresso","jogo"],
    }
    # Regras explícitas têm prioridade sobre o histórico aprendido.
    # Ex.: "gasolina" sempre deve ser Transporte, mesmo se um lançamento antigo
    # com a mesma descrição tiver sido salvo por engano em Moradia.
    for categoria, termos in regras.items():
        if any(t in texto for t in termos):
            return categoria

    aprendida=categoria_aprendida(descricao)
    if aprendida:
        return aprendida
    return "Outros"

menu_visivel=["🏠 Início","🏦 Bancos","🤖 MEFI","📱 WhatsApp","🚦 Saúde Financeira","📉 Dívidas","🎯 Metas"]

# Compatibilidade com sessões abertas antes da identidade MEFI.
if st.session_state.get("_page_atual")=="🤖 Assistente IA":
    st.session_state["_page_atual"]="🤖 MEFI"

# V45 — navegação persistente.
# Páginas abertas pelos atalhos do Início precisam continuar abertas durante
# qualquer rerun do Streamlit (digitação, radio, number_input, date_input etc.).
page_destino=st.session_state.pop("_page_destino",None)
if page_destino:
    st.session_state["_page_atual"]=page_destino

page_atual=st.session_state.get("_page_atual","🏠 Início")

if page_atual in menu_visivel:
    idx=menu_visivel.index(page_atual)
    page_menu=st.sidebar.radio("Menu",menu_visivel,index=idx,key="_menu_principal")
    if page_menu != page_atual:
        st.session_state["_page_atual"]=page_menu
        page_atual=page_menu

page=page_atual
st.sidebar.caption("✨ Menu simplificado: o uso do dia a dia fica concentrado no Início.")

if st.sidebar.button("❓ Tutorial / Ajuda", use_container_width=True):
    tutorial_dialog()

if not st.session_state.tutorial_oculto and not st.session_state.get("tutorial_mostrado_sessao", False):
    st.session_state.tutorial_mostrado_sessao = True
    tutorial_dialog()

if st.sidebar.button("🚪 Sair",use_container_width=True):
    try:
        sb.auth.sign_out()
    except Exception:
        pass
    st.session_state.clear()
    st.rerun()
st.sidebar.divider();st.sidebar.caption("🔒 Cada conta acessa somente os próprios dados.")

if page=="🏠 Início":

    st.title("Meu Financeiro")
    st.caption("Seu dinheiro, seus objetivos e o próximo pagamento em um só lugar.")
    # V50 — Primeiros Passos: aparece somente enquanto a configuração inicial estiver incompleta.
    try:
        _bancos_inicio=(sb.table("bancos").select("id").eq("user_id",st.session_state.uid).limit(1).execute().data or [])
    except Exception:
        _bancos_inicio=[]
    _tem_renda=family_income_total(cfg)>0
    _tem_banco=bool(_bancos_inicio)
    try:
        _tem_compromisso=bool(myrows("contas") or myrows("recorrentes") or myrows("parcelamentos"))
    except Exception:
        _tem_compromisso=False
    _passos_ok=sum([_tem_renda,_tem_banco,_tem_compromisso])

    if _passos_ok<3:
        st.markdown("### 👋 Primeiros passos")
        st.caption("Configure o essencial. Esta ajuda desaparece automaticamente quando os 3 passos estiverem concluídos.")
        st.progress(_passos_ok/3,text=f"{_passos_ok}/3 concluídos")
        op1,op2,op3=st.columns(3)
        with op1:
            st.markdown(("✅" if _tem_renda else "1️⃣")+" **Configure sua renda**")
            st.caption("Diga quanto entra no mês para o sistema calcular seu saldo.")
            if not _tem_renda and st.button("Configurar renda",use_container_width=True,key="onboard_renda"):
                st.session_state["_page_destino"]="⚙️ Minha renda";st.rerun()
        with op2:
            st.markdown(("✅" if _tem_banco else "2️⃣")+" **Adicione seu banco**")
            st.caption("Cadastre sua conta para organizar onde está seu dinheiro.")
            if not _tem_banco and st.button("Adicionar banco",use_container_width=True,key="onboard_banco"):
                st.session_state["_page_destino"]="🏦 Bancos";st.rerun()
        with op3:
            st.markdown(("✅" if _tem_compromisso else "3️⃣")+" **Adicione um compromisso**")
            st.caption("Cadastre uma conta ou parcelamento que você precisa pagar.")
            if not _tem_compromisso and st.button("Adicionar compromisso",use_container_width=True,key="onboard_comp"):
                st.session_state["_page_destino"]="🧾 Tenho uma conta";st.rerun()
        st.divider()
    if st.session_state.get("_flash_inicio"):
        st.success(st.session_state.pop("_flash_inicio"))
    t=date.today();ini=t.replace(day=1);fim=date(t.year,t.month,calendar.monthrange(t.year,t.month)[1])
    mov=pd.DataFrame(myrows("mov","data"))
    if len(mov):
        mov["data"]=pd.to_datetime(mov["data"]).dt.date
        mm=mov[(mov.data>=ini)&(mov.data<=fim)]
    else:mm=pd.DataFrame()
    gastos=float(mm.loc[mm.tipo=="Saída","valor"].sum()) if len(mm) else 0
    extras=float(mm.loc[mm.tipo=="Entrada","valor"].sum()) if len(mm) else 0
    sal=family_income_total(cfg);guardar=sal*float(cfg["pct"])/100;viver=sal-guardar
    nd,nv,_=next_family_income(cfg);dias=max((nd-t).days,0)
    contas=myrows("contas");pend=sum(float(x["valor"]) for x in contas if x["status"]!="Pago")
    cards=myrows("cartoes");fatura=sum(float(x["fatura"]) for x in cards)
    _,rec_total,rec_pendente=recurring_summary()
    _,parc_pendente=installment_summary()
    # Saldo realmente livre para uso: renda disponível menos gastos já feitos e compromissos ainda pendentes.
    saldo=max(viver+extras-gastos-pend-rec_pendente-parc_pendente-fatura,0)
    # Contas e recorrentes já foram descontados de saldo; não descontar novamente no limite diário.
    livre=max(saldo-float(cfg["reserva"]),0);diario=livre/max(dias,1)

    # V52 — Resumo simples: traduz os números principais em linguagem direta.
    # Reaproveita os cálculos do próprio Início, evitando depender de função externa.
    _saldo_bruto_inicio=viver+extras-gastos-pend-rec_pendente-parc_pendente-fatura
    _saldo_inicio=max(_saldo_bruto_inicio,0)
    _pend_inicio=pend+rec_pendente+parc_pendente+fatura
    _atrasadas_inicio=0
    for _c in [x for x in myrows("contas","vencimento") if x.get("status")!="Pago"]:
        try:
            if pd.to_datetime(_c.get("vencimento")).date()<date.today(): _atrasadas_inicio+=1
        except Exception: pass
    _rec_inicio,_,_=recurring_summary()
    for _r in _rec_inicio:
        if _r.get("ultimo_pago_mes")!=date.today().strftime("%Y-%m") and int(_r.get("dia_vencimento") or 1)<date.today().day: _atrasadas_inicio+=1
    _parc_inicio,_=installment_summary()
    for _p in _parc_inicio:
        if _p.get("ultimo_pago_mes")!=date.today().strftime("%Y-%m") and int(_p.get("dia_vencimento") or 1)<date.today().day: _atrasadas_inicio+=1

    # V54 — Cabeçalho financeiro unificado: alerta + indicadores sem duplicação.
    if _saldo_bruto_inicio>=0 and _atrasadas_inicio==0:
        _status_mes="✅ Tudo em dia — seu dinheiro está dentro do planejado."
        _status_tipo="success"
    elif _saldo_bruto_inicio>=0:
        _status_mes=f"⚠️ Atenção: você tem {_atrasadas_inicio} compromisso(s) atrasado(s), mas seu saldo planejado continua positivo."
        _status_tipo="warning"
    else:
        _status_mes="🔴 Atenção: seus compromissos ultrapassaram o valor disponível planejado para este mês."
        _status_tipo="error"

    # V53 — Painel Principal: uso diário concentrado em uma única visão.
    st.markdown("### 💰 Meu mês")
    _entradas_mes=sal+extras
    _gastos_mes=gastos
    _falta_pagar=pend+rec_pendente+parc_pendente+fatura

    if _status_tipo=="success":
        st.success(_status_mes)
    elif _status_tipo=="warning":
        st.warning(_status_mes)
    else:
        st.error(_status_mes)

    d1,d2,d3,d4,d5,d6=st.columns(6)
    d1.metric("💵 Disponível",money(saldo))
    d2.metric("📥 Entrou",money(_entradas_mes))
    d3.metric("📤 Gastou",money(_gastos_mes))
    d4.metric("🧾 Falta pagar",money(_falta_pagar))
    d5.metric("🐷 Guardar",money(guardar))
    d6.metric("⏰ Atrasados",str(_atrasadas_inicio))

    top_extra1,top_extra2=st.columns(2)
    top_extra1.metric("📅 Próximo pagamento",money(nv),help=f"Previsão: {nd.strftime('%d/%m/%Y')}")
    top_extra2.metric("📈 Limite seguro/dia",money(diario),help="Valor diário calculado até o próximo pagamento, preservando sua reserva.")
    st.caption("Resumo do mês considerando renda, movimentações, valor para guardar e compromissos pendentes.")

    st.markdown("### ⚡ Ações rápidas")
    st.caption("Faça o que precisa sem procurar em vários menus.")
    qa1,qa2,qa3,qa4,qa5,qa6=st.columns(6)
    if qa1.button("🎙️ MEFI",use_container_width=True,key="dash_inteligente"):
        st.session_state["_page_destino"]="🎙️ Lançamento inteligente";st.rerun()
    if qa2.button("➕ Receber",use_container_width=True,key="dash_receber"): quick_income_dialog()
    if qa3.button("➖ Gastar",use_container_width=True,key="dash_gastar"): quick_expense_dialog()
    if qa4.button("🧾 Nova conta",use_container_width=True,key="dash_conta"): quick_bill_dialog()
    if qa5.button("🛍️ Posso comprar?",use_container_width=True,key="dash_comprar"):
        st.session_state["_page_destino"]="🛍️ Posso comprar?";st.rerun()
    if qa6.button("⚙️ Minha renda",use_container_width=True,key="dash_renda"):
        st.session_state["_page_destino"]="⚙️ Minha renda";st.rerun()

    st.divider()

    # Movimentações recentes + gráfico por categoria, inspirados no fluxo visual do vídeo.
    painel_esq,painel_dir=st.columns([1.65,1])
    with painel_esq:
        st.markdown("### 🧾 Movimentações recentes")
        _busca=st.text_input("🔎 Buscar movimentação",placeholder="Ex.: mercado, Uber...",key="dash_busca_mov",label_visibility="collapsed")
        if len(mm):
            _recent=mm.copy()
            if _busca.strip():
                _q=_busca.lower().strip()
                _recent=_recent[_recent.apply(lambda r: _q in str(r.get("descricao","")).lower() or _q in str(r.get("categoria","")).lower(),axis=1)]
            _recent=_recent.sort_values("data",ascending=False).head(6)
            for _,_m in _recent.iterrows():
                _tipo=str(_m.get("tipo") or "")
                _ico="📥" if _tipo=="Entrada" else "📤"
                _nome=str(_m.get("descricao") or _m.get("categoria") or "Movimentação")
                _val=float(_m.get("valor") or 0)
                _dt=_m.get("data")
                _dt_txt=_dt.strftime("%d/%m") if hasattr(_dt,"strftime") else str(_dt)
                r1,r2=st.columns([4,1.3])
                r1.markdown(f"{_ico} **{_nome}**  \n<small>{_dt_txt} · {_tipo}</small>",unsafe_allow_html=True)
                r2.markdown(f"**{money(_val)}**")
        else:
            st.info("Ainda não há movimentações neste mês.")

    with painel_dir:
        st.markdown("### 🍩 Para onde foi meu dinheiro?")
        if len(mm):
            _saidas=mm[mm.tipo=="Saída"].copy()
        else:
            _saidas=pd.DataFrame()
        if len(_saidas):
            _saidas["categoria"]=_saidas["categoria"].fillna("Outros").replace("","Outros")
            _cat=_saidas.groupby("categoria",as_index=False)["valor"].sum().sort_values("valor",ascending=False)
            try:
                import altair as alt
                _donut=alt.Chart(_cat).mark_arc(innerRadius=58).encode(
                    theta=alt.Theta("valor:Q"),
                    color=alt.Color("categoria:N",legend=alt.Legend(title=None,orient="bottom")),
                    tooltip=[alt.Tooltip("categoria:N",title="Categoria"),alt.Tooltip("valor:Q",title="Valor",format=",.2f")]
                ).properties(height=260)
                st.altair_chart(_donut,use_container_width=True)
            except Exception:
                st.dataframe(_cat.rename(columns={"categoria":"Categoria","valor":"Valor"}),use_container_width=True,hide_index=True)
        else:
            st.info("Registre um gasto para ver o gráfico por categoria.")

    st.divider()

    try:
        _metas_home=myrows("metas")
    except Exception:
        _metas_home=[]
    if _metas_home:
        _meta_top=min(_metas_home,key=lambda m:max(float(m.get("desejado") or 0)-float(m.get("guardado") or 0),0))
        _md=float(_meta_top.get("desejado") or 0);_mg=float(_meta_top.get("guardado") or 0);_mp=min(_mg/_md,1.0) if _md>0 else 0
        st.markdown("### 🎯 Meta em destaque")
        mh1,mh2=st.columns([4,1])
        mh1.markdown(f"**{_meta_top.get('nome','Meta')}** · {int(_mp*100)}% concluído · faltam **{money(max(_md-_mg,0))}**")
        mh1.progress(_mp)
        if mh2.button("Ver metas",use_container_width=True,key="home_goals"):
            st.session_state["_page_destino"]="🎯 Metas";st.rerun()
        st.divider()

    st.markdown("### 🎯 Orçamento por categoria")
    st.caption("Veja rapidamente se alguma categoria está chegando ao limite do mês.")
    try:
        _orcs=myrows("orcamentos","categoria")
    except Exception:
        _orcs=[]
    if _orcs:
        _gastos_cat={}
        if len(mm):
            _s=mm[mm.tipo=="Saída"]
            if len(_s): _gastos_cat={str(k):float(v) for k,v in _s.groupby("categoria")["valor"].sum().to_dict().items()}
        for _o in _orcs:
            _cat=str(_o.get("categoria") or "Outros"); _lim=float(_o.get("limite") or 0); _usado=float(_gastos_cat.get(_cat,0))
            _pct_real=(_usado/_lim) if _lim>0 else 0
            _pct=min(_pct_real,1.0); _restante=max(_lim-_usado,0)
            bo1,bo2=st.columns([4,1])
            bo1.markdown(f"**{_cat}** · {int(_pct_real*100)}% usado · **{money(_restante)} restantes**")
            bo1.progress(_pct,text=f"{money(_usado)} de {money(_lim)}")
            if _lim>0 and _usado>_lim: bo2.error("🔴 Acima do orçamento")
            elif _lim>0 and _usado>=_lim*.8: bo2.warning("🟡 Perto do limite")
            else: bo2.success("🟢 Dentro do orçamento")
    else:
        st.info("Você ainda não definiu limites por categoria.")
    if st.button("🎯 Definir / alterar orçamento",use_container_width=True,key="dash_budget"):
        quick_budget_dialog()
    st.divider()

    st.markdown("### 📅 Contas e compromissos")
    st.caption("Veja rapidamente o que está atrasado, vence hoje e o que vem depois.")

    mes_comp=date.today().strftime("%Y-%m")
    rec_home,_,_=recurring_summary()
    parc_home,_=installment_summary()
    contas_home=[x for x in contas if x.get("status")!="Pago"]
    rec_home_pend=[x for x in rec_home if x.get("ultimo_pago_mes")!=mes_comp]
    parc_home_pend=[x for x in parc_home if x.get("ultimo_pago_mes")!=mes_comp]
    parc_home_fut=[x for x in parc_home if x.get("ultimo_pago_mes")==mes_comp and int(x.get("parcelas_pagas") or 0)<int(x.get("total_parcelas") or 0)]

    compromissos_home=[]
    for x in contas_home:
        try:
            dv=pd.to_datetime(x.get("vencimento")).date()
        except Exception:
            dv=date.today()
        compromissos_home.append(("conta",x,dv))
    for x in rec_home_pend:
        dia=max(1,min(int(x.get("dia_vencimento") or 1),calendar.monthrange(t.year,t.month)[1]))
        compromissos_home.append(("rec",x,date(t.year,t.month,dia)))
    for x in parc_home_pend:
        dia=max(1,min(int(x.get("dia_vencimento") or 1),calendar.monthrange(t.year,t.month)[1]))
        compromissos_home.append(("parc",x,date(t.year,t.month,dia)))

    compromissos_home.sort(key=lambda item:item[2])

    st.markdown("#### 🔔 Para pagar agora")
    if compromissos_home:
        for tipo_comp,x,dv_comp in compromissos_home[:6]:
            delta_comp=(dv_comp-t).days
            if delta_comp<0:
                status_comp=f"🔴 Atrasado há {abs(delta_comp)} dia(s)"
            elif delta_comp==0:
                status_comp="🟠 Vence hoje"
            elif delta_comp<=3:
                status_comp=f"🟡 Vence em {delta_comp} dia(s)"
            else:
                status_comp=f"🔵 Vence em {delta_comp} dia(s)"

            if tipo_comp=="conta":
                nome_comp=str(x.get("nome") or "Conta")
                valor_comp=float(x.get("valor") or 0)
                detalhe_comp=f"🧾 {nome_comp} · {money(valor_comp)} · {dv_comp.strftime('%d/%m')}"
                chave=f"home_pay_conta_{x['id']}"
            elif tipo_comp=="rec":
                nome_comp=str(x.get("nome") or "Todo mês")
                valor_comp=float(x.get("valor") or 0)
                detalhe_comp=f"🔁 {nome_comp} · {money(valor_comp)} · {dv_comp.strftime('%d/%m')}"
                chave=f"home_pay_rec_{x['id']}"
            else:
                nome_comp=str(x.get("nome") or "Compra parcelada")
                valor_comp=float(x.get("valor_parcela") or 0)
                total_comp=int(x.get("total_parcelas") or 0)
                pagas_comp=int(x.get("parcelas_pagas") or 0)
                atual_comp=min(pagas_comp+1,total_comp)
                detalhe_comp=f"💳 {nome_comp} · parcela {atual_comp}/{total_comp} · {money(valor_comp)} · {dv_comp.strftime('%d/%m')}"
                chave=f"home_pay_parc_{x['id']}"

            ca,cb,cc=st.columns([2,5,1.35])
            ca.markdown(f"**{status_comp}**")
            cb.markdown(f"**{detalhe_comp}**")
            if cc.button("✅ Paguei",key=chave,use_container_width=True):
                if tipo_comp=="conta":
                    sb.table("contas").update({"status":"Pago"}).eq("id",x["id"]).eq("user_id",st.session_state.uid).execute()
                elif tipo_comp=="rec":
                    sb.table("recorrentes").update({"ultimo_pago_mes":mes_comp}).eq("id",x["id"]).eq("user_id",st.session_state.uid).execute()
                    sync_recurring_payments()
                else:
                    novas_pagas=min(pagas_comp+1,total_comp)
                    dados_up={"ultimo_pago_mes":mes_comp,"parcelas_pagas":novas_pagas}
                    if novas_pagas>=total_comp:
                        dados_up["ativo"]=False
                    sb.table("parcelamentos").update(dados_up).eq("id",x["id"]).eq("user_id",st.session_state.uid).execute()
                    sync_installment_payments()
                    st.session_state["_flash_inicio"]=f"✅ {nome_comp}: parcela {novas_pagas}/{total_comp} paga."
                st.cache_data.clear();st.rerun()

        if len(compromissos_home)>6:
            st.caption(f"+ {len(compromissos_home)-6} compromisso(s). Use os botões abaixo para ver todos.")
    else:
        st.success("✅ Nada pendente para pagar agora.")

    if parc_home_fut:
        with st.expander(f"📅 Próximos compromissos ({len(parc_home_fut)})",expanded=True):
            st.caption("Parcelas já pagas neste mês que continuam depois. Aqui é só para acompanhar.")
            for x in parc_home_fut[:5]:
                nome_fut=str(x.get("nome") or "Compra parcelada")
                valor_fut=float(x.get("valor_parcela") or 0)
                total_fut=int(x.get("total_parcelas") or 0)
                pagas_fut=int(x.get("parcelas_pagas") or 0)
                prox_fut=min(pagas_fut+1,total_fut)
                dia_fut=int(x.get("dia_vencimento") or 1)
                st.write(f"💳 **{nome_fut}** · próxima **{prox_fut}/{total_fut}** · {money(valor_fut)} · dia {dia_fut} do próximo mês")

    mc1,mc2,mc3,mc4=st.columns(4)
    if mc1.button("🧾 Contas",use_container_width=True,key="home_contas"):
        st.session_state["_page_destino"]="🧾 Contas"; st.rerun()
    if mc2.button("🔁 Todo mês",use_container_width=True,key="home_recorrentes"):
        st.session_state["_page_destino"]="🔁 Recorrentes"; st.rerun()
    if mc3.button("💳 Parceladas",use_container_width=True,key="home_parcelamentos"):
        st.session_state["_page_destino"]="💳 Parcelamentos"; st.rerun()
    if mc4.button("💳 Meus cartões",use_container_width=True,key="home_cartoes"):
        st.session_state["_page_destino"]="💳 Cartões"; st.rerun()

    st.caption("💡 Os principais indicadores estão concentrados no painel **Meu mês**.")
    st.caption("🤖 Para uma análise detalhada, use **MEFI** no menu. Os números principais já estão em **Meu mês**.")

    # V13 — Vencimentos inteligentes: exibe somente compromissos ainda pendentes.
    vencimentos=[]
    for conta in contas:
        if conta.get("status") == "Pago":
            continue
        try:
            dv=pd.to_datetime(conta.get("vencimento")).date()
            vencimentos.append({"nome":conta.get("nome") or "Conta","valor":float(conta.get("valor") or 0),"data":dv,"origem":"Conta"})
        except Exception:
            pass
    mes_atual=t.strftime("%Y-%m")
    rec_itens,_,_=recurring_summary()
    for rec in rec_itens:
        if rec.get("ultimo_pago_mes") == mes_atual:
            continue
        try:
            dia_rec=max(1,min(int(rec.get("dia_vencimento") or 1),calendar.monthrange(t.year,t.month)[1]))
            dv=date(t.year,t.month,dia_rec)
            vencimentos.append({"nome":rec.get("nome") or "Recorrente","valor":float(rec.get("valor") or 0),"data":dv,"origem":"Recorrente"})
        except Exception:
            pass
    parc_itens,_=installment_summary()
    for parc in parc_itens:
        if parc.get("ultimo_pago_mes") == mes_atual:
            continue
        try:
            dia_parc=max(1,min(int(parc.get("dia_vencimento") or 1),calendar.monthrange(t.year,t.month)[1]))
            dv=date(t.year,t.month,dia_parc)
            atual=int(parc.get("parcelas_pagas") or 0)+1
            total=int(parc.get("total_parcelas") or 0)
            vencimentos.append({"nome":f"{parc.get('nome') or 'Parcelamento'} ({atual}/{total})","valor":float(parc.get("valor_parcela") or 0),"data":dv,"origem":"Parcelamento"})
        except Exception:
            pass
    vencimentos.sort(key=lambda x:x["data"])
    if vencimentos:
        st.subheader("🔔 Próximos vencimentos")
        st.caption("Contas, recorrentes e parcelas ainda não pagos. Itens pagos no mês deixam de aparecer aqui automaticamente.")
        for item in vencimentos[:8]:
            delta=(item["data"]-t).days
            if delta < 0:
                texto=f"🔴 **{item['nome']} — {money(item['valor'])}** · atrasado há {abs(delta)} dia(s) · venceu em {item['data'].strftime('%d/%m')}"
                st.error(texto)
            elif delta == 0:
                st.warning(f"🟠 **{item['nome']} — {money(item['valor'])}** · vence hoje")
            elif delta <= 3:
                st.warning(f"🟡 **{item['nome']} — {money(item['valor'])}** · vence em {delta} dia(s) ({item['data'].strftime('%d/%m')})")
            elif delta <= 7:
                st.info(f"🔵 **{item['nome']} — {money(item['valor'])}** · vence em {delta} dia(s) ({item['data'].strftime('%d/%m')})")
            else:
                st.write(f"⚪ **{item['nome']} — {money(item['valor'])}** · vence em {delta} dia(s) ({item['data'].strftime('%d/%m')})")
    else:
        st.success("🔔 Nenhuma conta, recorrente ou parcela pendente para vencer neste mês.")

    if len(mm):
        s=mm[mm.tipo=="Saída"].groupby("categoria")["valor"].sum().reset_index()
        if len(s):
            st.subheader("Gastos por categoria")
            graf=alt.Chart(s).mark_bar(cornerRadiusTopLeft=6,cornerRadiusTopRight=6).encode(
                x=alt.X("categoria:N",title=None,sort="-y"),
                y=alt.Y("valor:Q",title="Valor (R$)"),
                tooltip=[alt.Tooltip("categoria:N",title="Categoria"),alt.Tooltip("valor:Q",title="Valor",format=",.2f")]
            ).properties(height=300,background="white").configure_axis(
                labelColor="#172033",titleColor="#172033",gridColor="#E7EBF2"
            ).configure_view(strokeOpacity=0)
            st.altair_chart(graf,use_container_width=True)


elif page=="💰 Recebi dinheiro":
    st.title("💰 Recebi dinheiro")
    st.caption("Registre uma entrada de dinheiro de forma rápida.")
    if st.button("← Voltar ao Início",key="sr_income_back"):
        st.session_state["_page_destino"]="🏠 Início"; st.rerun()
    st.divider()

    if st.session_state.get("_simple_income_ok"):
        info=st.session_state["_simple_income_ok"]
        st.success("✅ Entrada registrada com sucesso.")
        st.metric("Valor recebido",money(info["valor"]))
        st.write(f"**{info['descricao']}**")
        a,b=st.columns(2)
        if a.button("🏠 Voltar ao Início",use_container_width=True,key="sr_income_home"):
            st.session_state.pop("_simple_income_ok",None)
            st.session_state["_page_destino"]="🏠 Início";st.rerun()
        if b.button("➕ Registrar outra entrada",use_container_width=True,key="sr_income_again"):
            st.session_state.pop("_simple_income_ok",None);st.rerun()
        st.stop()

    with st.form("simple_income_form",clear_on_submit=True):
        si_desc=st.text_input("De onde veio o dinheiro?",placeholder="Ex.: Salário extra, venda, reembolso")
        si_valor=st.number_input("Quanto você recebeu?",min_value=0.0,step=50.0,format="%.2f")
        si_data=st.date_input("Quando recebeu?",value=date.today())
        si_forma=st.selectbox("Como recebeu?",["Pix","Dinheiro","Transferência","Outro"])
        if st.form_submit_button("✅ Registrar dinheiro recebido",use_container_width=True):
            if not si_desc.strip() or si_valor<=0:
                st.warning("Informe de onde veio o dinheiro e um valor maior que zero.")
            else:
                sb.table("mov").insert({
                    "user_id":st.session_state.uid,"data":si_data.isoformat(),
                    "descricao":si_desc.strip(),"categoria":"Outros","tipo":"Entrada",
                    "forma":si_forma,"valor":float(si_valor),"obs":""
                }).execute()
                st.cache_data.clear()
                st.session_state["_simple_income_ok"]={"descricao":si_desc.strip(),"valor":float(si_valor)}
                st.rerun()

elif page=="🛒 Gastei dinheiro":
    st.title("🛒 Gastei dinheiro")
    st.caption("Registre um gasto sem precisar preencher uma tela complicada.")
    if st.button("← Voltar ao Início",key="sr_expense_back"):
        st.session_state["_page_destino"]="🏠 Início"; st.rerun()
    st.divider()

    if st.session_state.get("_simple_expense_ok"):
        info=st.session_state["_simple_expense_ok"]
        st.success("✅ Gasto registrado com sucesso.")
        st.metric("Valor gasto",money(info["valor"]))
        st.write(f"**{info['descricao']}** · {info['categoria']}")
        a,b=st.columns(2)
        if a.button("🏠 Voltar ao Início",use_container_width=True,key="sr_expense_home"):
            st.session_state.pop("_simple_expense_ok",None)
            st.session_state["_page_destino"]="🏠 Início";st.rerun()
        if b.button("➕ Registrar outro gasto",use_container_width=True,key="sr_expense_again"):
            st.session_state.pop("_simple_expense_ok",None);st.rerun()
        st.stop()

    se_desc=st.text_input("Com o que você gastou?",placeholder="Ex.: Mercado, Uber, farmácia",key="simple_expense_desc")
    categorias_simples=["Moradia","Alimentação","Transporte","Saúde","Lazer","Assinaturas","Compras","Outros"]
    se_sugerida=sugerir_categoria(se_desc)
    se_idx=categorias_simples.index(se_sugerida) if se_sugerida in categorias_simples else len(categorias_simples)-1
    if se_desc.strip():
        st.caption(f"✨ Categoria sugerida: {se_sugerida}")
    with st.form("simple_expense_form",clear_on_submit=True):
        se_valor=st.number_input("Quanto você gastou?",min_value=0.0,step=20.0,format="%.2f")
        a,b=st.columns(2)
        se_cat=a.selectbox("Categoria",categorias_simples,index=se_idx)
        se_forma=b.selectbox("Como pagou?",["Pix","Débito","Crédito","Dinheiro","Outro"])
        se_data=st.date_input("Quando gastou?",value=date.today())
        if st.form_submit_button("✅ Registrar gasto",use_container_width=True):
            if not se_desc.strip() or se_valor<=0:
                st.warning("Informe o gasto e um valor maior que zero.")
            else:
                sb.table("mov").insert({
                    "user_id":st.session_state.uid,"data":se_data.isoformat(),
                    "descricao":se_desc.strip(),"categoria":se_cat,"tipo":"Saída",
                    "forma":se_forma,"valor":float(se_valor),"obs":""
                }).execute()
                st.cache_data.clear()
                st.session_state["_simple_expense_ok"]={"descricao":se_desc.strip(),"valor":float(se_valor),"categoria":se_cat}
                st.rerun()

elif page=="🧾 Tenho uma conta":
    st.title("🧾 Tenho uma conta")
    st.caption("Cadastre uma conta que você ainda precisa pagar.")
    if st.button("← Voltar ao Início",key="sr_bill_back"):
        st.session_state["_page_destino"]="🏠 Início"; st.rerun()
    st.divider()

    if st.session_state.get("_simple_bill_ok"):
        info=st.session_state["_simple_bill_ok"]
        st.success("✅ Conta adicionada aos seus compromissos.")
        a,b=st.columns(2)
        a.metric("Valor",money(info["valor"]))
        b.metric("Vencimento",info["vencimento"])
        st.write(f"**{info['nome']}**")
        c,d=st.columns(2)
        if c.button("🏠 Voltar ao Início",use_container_width=True,key="sr_bill_home"):
            st.session_state.pop("_simple_bill_ok",None)
            st.session_state["_page_destino"]="🏠 Início";st.rerun()
        if d.button("➕ Adicionar outra conta",use_container_width=True,key="sr_bill_again"):
            st.session_state.pop("_simple_bill_ok",None);st.rerun()
        st.stop()

    with st.form("simple_bill_form",clear_on_submit=True):
        sb_nome=st.text_input("Qual é a conta?",placeholder="Ex.: Energia, celular, escola")
        sb_valor=st.number_input("Qual o valor?",min_value=0.0,step=20.0,format="%.2f")
        a,b=st.columns(2)
        sb_venc=a.date_input("Quando vence?",value=date.today())
        sb_cat=b.selectbox("Categoria",["Moradia","Saúde","Transporte","Assinaturas","Outros"])
        if st.form_submit_button("✅ Adicionar conta",use_container_width=True):
            if not sb_nome.strip() or sb_valor<=0:
                st.warning("Informe o nome da conta e um valor maior que zero.")
            else:
                sb.table("contas").insert({
                    "user_id":st.session_state.uid,"nome":sb_nome.strip(),"categoria":sb_cat,
                    "valor":float(sb_valor),"vencimento":sb_venc.isoformat(),"status":"Pendente"
                }).execute()
                st.cache_data.clear()
                st.session_state["_simple_bill_ok"]={
                    "nome":sb_nome.strip(),"valor":float(sb_valor),
                    "vencimento":sb_venc.strftime("%d/%m/%Y")
                }
                st.rerun()


elif page=="⚙️ Minha renda":
    st.title("⚙️ Rendas da família")
    st.caption("Cadastre quantos salários e rendas planejadas quiser, separados por titular.")
    if st.button("← Voltar ao Início",key="mr_back"):
        st.session_state["_page_destino"]="🏠 Início"; st.rerun()
    rendas=income_rows(); ativas=[x for x in rendas if bool(x.get("ativo",True)) and bool(x.get("incluir_familiar",True))]
    a,b,c=st.columns(3); a.metric("Renda familiar",money(sum(float(x.get("valor") or 0) for x in ativas))); b.metric("Fontes ativas",len(ativas)); c.metric("Titulares",len(set(str(x.get("titular") or "Titular") for x in ativas)))
    st.markdown("### ➕ Adicionar renda")
    with st.form("nova_renda",clear_on_submit=True):
        a,b=st.columns(2); titular=a.text_input("Titular",placeholder="Ex.: Gabriel, esposa"); descricao=b.text_input("Fonte da renda",placeholder="Ex.: Salário, adiantamento")
        c,d,e=st.columns(3); valor=c.number_input("Valor mensal",min_value=0.0,step=100.0,format="%.2f"); dia=d.number_input("Dia de recebimento",1,28,5); tipo=e.selectbox("Tipo",["Fixa","Variável planejada"])
        incluir=st.checkbox("Incluir na renda familiar",value=True)
        if st.form_submit_button("💾 Adicionar renda",use_container_width=True):
            if not titular.strip() or not descricao.strip() or valor<=0: st.warning("Informe titular, fonte e valor maior que zero.")
            else:
                sb.table("rendas").insert({"user_id":st.session_state.uid,"titular":titular.strip(),"descricao":descricao.strip(),"valor":float(valor),"dia_recebimento":int(dia),"tipo":tipo,"incluir_familiar":bool(incluir),"ativo":True}).execute(); st.cache_data.clear(); st.rerun()
    st.markdown("### 👥 Rendas cadastradas")
    if not rendas: st.info("Nenhuma renda cadastrada.")
    for r in rendas:
        with st.expander(f"{'🟢' if r.get('ativo',True) else '⚪'} {r.get('titular','Titular')} · {r.get('descricao','Renda')} — {money(r.get('valor',0))}"):
            a,b=st.columns(2); nt=a.text_input("Titular",value=str(r.get("titular") or ""),key=f"ri_t_{r['id']}"); nd=b.text_input("Fonte",value=str(r.get("descricao") or ""),key=f"ri_d_{r['id']}")
            c,d,e=st.columns(3); nv=c.number_input("Valor",min_value=0.0,value=float(r.get("valor") or 0),step=100.0,key=f"ri_v_{r['id']}"); ndi=d.number_input("Dia",1,28,int(r.get("dia_recebimento") or 5),key=f"ri_di_{r['id']}"); ntipo=e.selectbox("Tipo",["Fixa","Variável planejada"],index=0 if r.get("tipo")!="Variável planejada" else 1,key=f"ri_tp_{r['id']}")
            inc=st.checkbox("Incluir na renda familiar",value=bool(r.get("incluir_familiar",True)),key=f"ri_i_{r['id']}"); ativo=st.checkbox("Renda ativa",value=bool(r.get("ativo",True)),key=f"ri_a_{r['id']}")
            x,y=st.columns(2)
            if x.button("💾 Salvar",use_container_width=True,key=f"ri_s_{r['id']}"):
                sb.table("rendas").update({"titular":nt.strip(),"descricao":nd.strip(),"valor":float(nv),"dia_recebimento":int(ndi),"tipo":ntipo,"incluir_familiar":bool(inc),"ativo":bool(ativo)}).eq("id",r["id"]).eq("user_id",st.session_state.uid).execute(); st.cache_data.clear(); st.rerun()
            if y.button("🗑️ Excluir",use_container_width=True,key=f"ri_x_{r['id']}"):
                sb.table("rendas").delete().eq("id",r["id"]).eq("user_id",st.session_state.uid).execute(); st.cache_data.clear(); st.rerun()
    st.divider(); st.markdown("### 🐷 Planejamento")
    p1,p2=st.columns(2); pct_guardar=p1.slider("Quanto quer guardar por mês? (%)",0,50,int(cfg["pct"]),key="mr_pct_multi"); reserva_min=p2.number_input("Reserva mínima",min_value=0.0,value=float(cfg["reserva"]),step=100.0,format="%.2f",key="mr_res_multi")
    if st.button("💾 Salvar planejamento",use_container_width=True,key="mr_plan_save"):
        sb.table("config").update({"pct":int(pct_guardar),"reserva":float(reserva_min)}).eq("user_id",st.session_state.uid).execute(); st.cache_data.clear(); st.session_state["_flash_inicio"]="✅ Planejamento de renda atualizado."; st.session_state["_page_destino"]="🏠 Início"; st.rerun()

elif page=="🎙️ Lançamento inteligente":
    st.title("🎙️ MEFI — Lançamento inteligente")
    st.caption("Fale ou digite como você fala no dia a dia. O MEFI interpreta localmente, sem API paga, e pede sua confirmação antes de salvar.")
    if st.button("← Voltar ao Início",key="li_back"):
        st.session_state["_page_destino"]="🏠 Início"; st.rerun()
    st.info("Exemplos: ‘Recebi 15 reais de Uber no Pix’, ‘Gastei 15 com energético’, ‘Paguei 136 da conta de água’. Confira antes de confirmar.")
    if voice_input_component is not None:
        vr=voice_input_component(data={},default={"transcript":"","voice_error":""},key="voice_input_v671",on_transcript_change=lambda: None,on_voice_error_change=lambda: None)
        voz=getattr(vr,"transcript",None)
        if voz and voz!=st.session_state.get("_li_last_voice"):
            st.session_state["_li_last_voice"]=voz
            st.session_state["_li_texto"]=voz
            st.session_state["li_text_input"]=voz
            st.session_state["_li_preview"]=interpretar_lancamento(voz)
            st.rerun()
    # V3 — Enter interpreta o lançamento automaticamente.
    # O form faz o teclado físico e o botão Enviar/Enter do celular executarem
    # a mesma ação, sem exigir um clique extra em "Entender lançamento".
    with st.form("li_parse_form", clear_on_submit=False):
        comando=st.text_input(
            "O que aconteceu?",
            value=st.session_state.get("_li_texto",""),
            placeholder="Ex.: Recebi 30 reais de Uber em dinheiro",
            key="li_text_input"
        )
        st.caption("⌨️ Digite seu lançamento e aperte **Enter** para continuar.")
        interpretar_agora=st.form_submit_button("✨ Entender lançamento",use_container_width=True)

    st.caption("🎤 O botão usa o reconhecimento de voz do próprio navegador quando disponível. Se o aparelho não oferecer suporte, use o microfone do teclado ou digite. O Meu Financeiro não envia esse áudio para uma API paga.")

    if interpretar_agora:
        st.session_state["_li_texto"]=comando
        st.session_state["_li_preview"]=interpretar_lancamento(comando)
        st.rerun()
    info=st.session_state.get("_li_preview")
    if info:
        st.markdown("### ✨ O MEFI entendeu assim")
        d,e=st.columns(2); desc=d.text_input("Descrição",value=info["descricao"],key="li_desc_confirm"); cats=["Moradia","Alimentação","Transporte","Saúde","Lazer","Assinaturas","Compras","Renda variável","Outros"]; categoria_inicial=info["categoria"] if info["categoria"] in cats else ("Moradia" if info["categoria"]=="Contas" else "Outros"); cat=e.selectbox("Categoria",cats,index=cats.index(categoria_inicial),key="li_cat_confirm")
        f,g,h=st.columns(3); tipo=f.selectbox("Tipo",["Entrada","Saída"],index=0 if info["tipo"]=="Entrada" else 1,key="li_tipo_confirm"); forma_opts=["Pix","Dinheiro","Débito","Crédito","Transferência","Outro"]; forma=g.selectbox("Forma",forma_opts,index=forma_opts.index(info["forma"]) if info["forma"] in forma_opts else len(forma_opts)-1,key="li_forma_confirm"); dt=h.date_input("Data",value=info["data"],key="li_data_confirm")
        # O resumo usa os valores finais dos campos. Se o usuário corrigir Tipo/Categoria,
        # o Streamlit reroda e o resumo acompanha imediatamente, sem mostrar dados contraditórios.
        a,b,c=st.columns(3); a.metric("Movimento",("📥 Entrada" if tipo=="Entrada" else "📤 Saída")); b.metric("Valor",money(info["valor"])); c.metric("Categoria",cat)
        if info["valor"]<=0: st.warning("Não consegui identificar o valor. Digite a frase novamente incluindo o valor.")
        if st.button("✅ Confirmar e lançar",use_container_width=True,type="primary",key="li_confirm"):
            if info["valor"]<=0 or not desc.strip(): st.warning("Confira o valor e a descrição.")
            else:
                sb.table("mov").insert({"user_id":st.session_state.uid,"data":dt.isoformat(),"descricao":desc.strip(),"categoria":cat,"tipo":tipo,"forma":forma,"valor":float(info["valor"]),"obs":"LANCAMENTO_INTELIGENTE"}).execute(); st.cache_data.clear(); st.session_state.pop("_li_preview",None); st.session_state["_flash_inicio"]=f"✅ {tipo} de {money(info['valor'])} registrada por lançamento inteligente."; st.session_state["_page_destino"]="🏠 Início"; st.rerun()

elif page=="🛍️ Posso comprar?":
    compra_ok=st.session_state.get("_compra_confirmada")
    if compra_ok:
        st.title("✅ Compra adicionada com sucesso")
        st.success("Pronto! A compra já foi adicionada ao seu Meu Financeiro.")
        st.markdown(f"### {compra_ok.get('nome','Compra')}")
        c1,c2,c3=st.columns(3)
        c1.metric("Valor da compra",money(float(compra_ok.get("valor_total",0))))
        if compra_ok.get("forma")=="Parcelado":
            c2.metric("Parcela mensal",money(float(compra_ok.get("parcela",0))))
            c3.metric("Quantidade",f"{int(compra_ok.get('parcelas',1))}x")
            st.info(f"📅 Primeira parcela: **{compra_ok.get('primeiro_vencimento','—')}**. Ela já entra nos seus compromissos.")
        else:
            c2.metric("Pagamento","À vista")
            c3.metric("Registrado como","Gasto")
        a,b=st.columns(2)
        if a.button("🏠 Voltar ao Início",use_container_width=True,key="pc_ok_home"):
            st.session_state.pop("_compra_confirmada",None)
            st.session_state["_page_destino"]="🏠 Início"
            st.rerun()
        if b.button("🛍️ Simular outra compra",use_container_width=True,key="pc_ok_again"):
            st.session_state.pop("_compra_confirmada",None)
            st.rerun()
        st.stop()

    st.title("🛍️ Posso comprar?")
    st.caption("Simule antes de comprar. Nada é lançado no seu financeiro até você confirmar.")
    if st.button("← Voltar ao Início",key="pc_back"):
        st.session_state["_page_destino"]="🏠 Início"
        st.rerun()
    st.divider()

    t=date.today();ini=t.replace(day=1);fim=date(t.year,t.month,calendar.monthrange(t.year,t.month)[1])
    mov_pc=pd.DataFrame(myrows("mov","data"))
    if len(mov_pc):
        mov_pc["data"]=pd.to_datetime(mov_pc["data"]).dt.date
        mm_pc=mov_pc[(mov_pc.data>=ini)&(mov_pc.data<=fim)]
    else:
        mm_pc=pd.DataFrame()
    gastos_pc=float(mm_pc.loc[mm_pc.tipo=="Saída","valor"].sum()) if len(mm_pc) else 0
    extras_pc=float(mm_pc.loc[mm_pc.tipo=="Entrada","valor"].sum()) if len(mm_pc) else 0
    renda_pc=family_income_total(cfg)
    guardar_pc=renda_pc*float(cfg["pct"])/100
    viver_pc=renda_pc-guardar_pc
    nd_pc,_,_=next_family_income(cfg);dias_pc=max((nd_pc-t).days,0)
    contas_pc=myrows("contas")
    pend_pc=sum(float(x["valor"]) for x in contas_pc if x["status"]!="Pago")
    cards_pc=myrows("cartoes");fatura_pc=sum(float(x["fatura"]) for x in cards_pc)
    _,_,rec_pend_pc=recurring_summary()
    _,parc_pend_pc=installment_summary()
    saldo_pc=max(viver_pc+extras_pc-gastos_pc-pend_pc-rec_pend_pc-parc_pend_pc-fatura_pc,0)
    livre_pc=max(saldo_pc-float(cfg["reserva"]),0)
    diario_pc=livre_pc/max(dias_pc,1)

    pc1,pc2=st.columns(2)
    pc_nome=pc1.text_input("O que você quer comprar?",placeholder="Ex.: Celular",key="pc_nome_page")
    pc_valor=pc2.number_input("Valor total da compra",min_value=0.0,step=50.0,format="%.2f",key="pc_valor_page")
    pc3,pc4=st.columns(2)
    pc_forma=pc3.radio("Como pretende pagar?",["À vista","Parcelado"],horizontal=True,key="pc_forma_page")
    pc_parcelas=1
    pc_primeiro_venc=None
    if pc_forma=="Parcelado":
        pc_parcelas=pc4.number_input("Quantidade de parcelas",min_value=2,max_value=60,value=10,step=1,key="pc_parcelas_page")
        pc_primeiro_venc=st.date_input("Quando vence a primeira parcela?",value=date.today(),key="pc_primeiro_venc_page")
    pc_parcela=(pc_valor/pc_parcelas) if pc_parcelas else pc_valor
    impacto=pc_valor if pc_forma=="À vista" else pc_parcela
    saldo_depois=saldo_pc-impacto

    a,b,c=st.columns(3)
    a.metric("Valor da compra",money(pc_valor))
    b.metric("Parcela mensal" if pc_forma=="Parcelado" else "Pagamento",money(impacto))
    c.metric("Saldo para usar após impacto",money(saldo_depois))

    if pc_valor>0:
        reserva_base=float(cfg["reserva"])
        margem_mensal=max(renda_pc-reserva_base-pend_pc-rec_pend_pc-parc_pend_pc-gastos_pc,0)
        if saldo_depois<0 or (pc_forma=="Parcelado" and pc_parcela>margem_mensal):
            st.error("🔴 Esta compra pressiona demais o orçamento atual. Revise o valor, as parcelas ou seus compromissos antes de comprar.")
        elif impacto>diario_pc*7 or (pc_forma=="Parcelado" and margem_mensal>0 and pc_parcela>margem_mensal*0.30):
            st.warning("🟡 A compra cabe, mas a parcela terá peso relevante nos próximos meses. Confira se quer assumir esse compromisso.")
        else:
            st.success("🟢 Considerando seu orçamento atual, esta compra cabe. O parcelamento continuará comprometendo os próximos meses até terminar.")
        if pc_forma=="Parcelado":
            st.caption(f"{int(pc_parcelas)}x de {money(pc_parcela)} · total de {money(pc_valor)} · compromisso por {int(pc_parcelas)} meses. Primeira parcela: {pc_primeiro_venc.strftime('%d/%m/%Y')}.")

        st.markdown("### Confirmar compra")
        st.caption("Só confirme se a compra realmente foi feita.")
        if pc_forma=="À vista":
            if st.button("✅ Comprei — registrar gasto",use_container_width=True,key="pc_confirm_cash_page"):
                try:
                    sb.table("mov").insert({
                        "user_id":st.session_state.uid,"tipo":"Saída","categoria":"Compras",
                        "descricao":pc_nome.strip() or "Compra simulada","valor":float(pc_valor),
                        "data":date.today().isoformat()
                    }).execute()
                    st.cache_data.clear()
                    st.session_state["_compra_confirmada"]={
                        "nome":pc_nome.strip() or "Compra",
                        "forma":"À vista",
                        "valor_total":float(pc_valor)
                    }
                    st.rerun()
                except Exception:
                    st.error("Não foi possível registrar automaticamente.")
        else:
            if st.button("✅ Comprei — criar parcelamento",use_container_width=True,key="pc_confirm_installment_page"):
                try:
                    sb.table("parcelamentos").insert({
                        "user_id":st.session_state.uid,"nome":pc_nome.strip() or "Compra parcelada",
                        "categoria":"Compras","valor_parcela":float(pc_parcela),
                        "total_parcelas":int(pc_parcelas),"parcelas_pagas":0,
                        "dia_vencimento":int(pc_primeiro_venc.day),"primeira_parcela":pc_primeiro_venc.isoformat(),
                        "forma":"Crédito","ativo":True
                    }).execute()
                    st.cache_data.clear()
                    st.session_state["_compra_confirmada"]={
                        "nome":pc_nome.strip() or "Compra parcelada",
                        "forma":"Parcelado",
                        "valor_total":float(pc_valor),
                        "parcela":float(pc_parcela),
                        "parcelas":int(pc_parcelas),
                        "primeiro_vencimento":pc_primeiro_venc.strftime("%d/%m/%Y")
                    }
                    st.rerun()
                except Exception:
                    st.error("Não foi possível criar o parcelamento automaticamente.")


elif page=="🏦 Bancos":
    st.title("🏦 Central de Bancos")
    st.caption("Organize contas manualmente ou teste a conexão automática com a Pluggy Sandbox.")
    st.info("🔒 O Meu Financeiro não pede senha bancária, CVV ou número completo do cartão. No fluxo conectado, a autenticação acontece dentro da interface segura do provedor.")

    try:
        bancos=(sb.table("bancos")
                  .select("*")
                  .eq("user_id",st.session_state.uid)
                  .order("created_at")
                  .execute().data or [])
    except Exception:
        bancos=[]

    ativos=[b for b in bancos if bool(b.get("ativo",True))]
    titulares=sorted({(b.get("titular") or "Sem titular").strip() for b in bancos})
    saldo_total=sum(float(b.get("saldo") or 0) for b in ativos)

    c1,c2,c3=st.columns(3)
    c1.metric("Contas cadastradas",len(ativos))
    c2.metric("Titulares",len({(b.get("titular") or "Sem titular").strip() for b in ativos}))
    c3.metric("Saldo familiar informado",money(saldo_total))

    if ativos:
        st.subheader("👥 Resumo por titular")
        cols=st.columns(min(4,max(1,len({(b.get("titular") or "Sem titular").strip() for b in ativos}))))
        resumo={}
        for b in ativos:
            t=(b.get("titular") or "Sem titular").strip()
            resumo[t]=resumo.get(t,0)+float(b.get("saldo") or 0)
        for i,(tit,val) in enumerate(sorted(resumo.items())):
            cols[i % len(cols)].metric(tit,money(val))

    st.subheader("➕ Adicionar conta bancária")
    with st.form("novo_banco",clear_on_submit=True):
        a,b=st.columns(2)
        titular=a.text_input("Titular",placeholder="Ex.: Gabriel ou nome da sua esposa")
        banco_nome=b.text_input("Banco / instituição",placeholder="Ex.: Nubank, Itaú, Inter")
        c,d=st.columns(2)
        apelido=c.text_input("Apelido da conta",placeholder="Ex.: Conta principal")
        tipo_conta=d.selectbox("Tipo de conta",["Conta corrente","Conta digital","Poupança","Conta salário","Investimentos","Outro"])
        e,f=st.columns(2)
        saldo_inicial=e.number_input("Saldo atual informado",min_value=0.0,step=50.0,format="%.2f")
        incluir=f.checkbox("Incluir no saldo familiar",value=True)
        salvar=st.form_submit_button("💾 Adicionar banco",use_container_width=True)

    if salvar:
        if not titular.strip():
            st.warning("Informe o titular da conta.")
        elif not banco_nome.strip():
            st.warning("Informe o nome do banco ou instituição.")
        else:
            try:
                sb.table("bancos").insert({
                    "user_id":st.session_state.uid,
                    "titular":titular.strip(),
                    "nome":banco_nome.strip(),
                    "apelido":apelido.strip(),
                    "tipo_conta":tipo_conta,
                    "saldo":float(saldo_inicial),
                    "ativo":bool(incluir)
                }).execute()
                st.cache_data.clear();st.success("Conta bancária adicionada.");st.rerun()
            except Exception:
                st.error("Não foi possível salvar. Confirme se o SQL da V29 foi executado no Supabase.")

    st.divider();st.subheader("🏦 Suas instituições")
    filtro_opcoes=["Todos"]+titulares
    filtro=st.selectbox("Mostrar contas de",filtro_opcoes,index=0)
    bancos_filtrados=bancos if filtro=="Todos" else [b for b in bancos if (b.get("titular") or "Sem titular").strip()==filtro]

    if not bancos_filtrados:
        st.info("Nenhuma conta bancária cadastrada para este filtro.")
    else:
        for bnk in bancos_filtrados:
            titulo=bnk.get("nome") or "Banco"
            tit=(bnk.get("titular") or "Sem titular").strip()
            apel=bnk.get("apelido") or ""
            tipo=bnk.get("tipo_conta") or "Conta"
            saldo=float(bnk.get("saldo") or 0)
            ativo=bool(bnk.get("ativo",True))
            rotulo=f"{'🟢' if ativo else '⚪'} {tit} · {titulo}"
            if apel: rotulo+=f" · {apel}"
            rotulo+=f" — {money(saldo)}"
            with st.expander(rotulo):
                x,y=st.columns(2)
                novo_titular=x.text_input("Titular",value=tit,key=f"bn_tit_{bnk['id']}")
                novo_nome=y.text_input("Banco / instituição",value=titulo,key=f"bn_nome_{bnk['id']}")
                z,w=st.columns(2)
                novo_apelido=z.text_input("Apelido",value=apel,key=f"bn_ap_{bnk['id']}")
                tipos=["Conta corrente","Conta digital","Poupança","Conta salário","Investimentos","Outro"]
                idx=tipos.index(tipo) if tipo in tipos else len(tipos)-1
                novo_tipo=w.selectbox("Tipo",tipos,index=idx,key=f"bn_tipo_{bnk['id']}")
                q,r=st.columns(2)
                novo_saldo=q.number_input("Saldo informado",min_value=0.0,value=saldo,step=50.0,format="%.2f",key=f"bn_saldo_{bnk['id']}")
                novo_ativo=r.checkbox("Incluir no saldo familiar",value=ativo,key=f"bn_ativo_{bnk['id']}")
                e1,e2=st.columns(2)
                if e1.button("💾 Salvar alterações",key=f"bn_save_{bnk['id']}",use_container_width=True):
                    if not novo_titular.strip():
                        st.warning("Informe o titular.")
                    else:
                        try:
                            sb.table("bancos").update({
                                "titular":novo_titular.strip(),
                                "nome":novo_nome.strip() or titulo,
                                "apelido":novo_apelido.strip(),
                                "tipo_conta":novo_tipo,
                                "saldo":float(novo_saldo),
                                "ativo":bool(novo_ativo)
                            }).eq("id",bnk["id"]).eq("user_id",st.session_state.uid).execute()
                            st.cache_data.clear();st.success("Banco atualizado.");st.rerun()
                        except Exception: st.error("Não foi possível atualizar esta conta.")
                if e2.button("🗑️ Excluir",key=f"bn_del_{bnk['id']}",use_container_width=True):
                    try:
                        sb.table("bancos").delete().eq("id",bnk["id"]).eq("user_id",st.session_state.uid).execute()
                        st.cache_data.clear();st.success("Conta removida.");st.rerun()
                    except Exception: st.error("Não foi possível excluir esta conta.")

    st.divider();st.subheader("🔗 Conexão bancária — Pluggy Sandbox")
    st.caption("Modo de desenvolvimento. O Meu Financeiro procura o Pluggy Bank diretamente pela API e abre somente o conector de teste.")
    st.info("🔒 Client ID e Client Secret ficam somente no servidor. O navegador recebe apenas o Connect Token temporário e o ID público do conector Sandbox.")

    if not pluggy_configured():
        st.warning("As credenciais Pluggy ainda não estão configuradas nos Secrets.")
    elif pluggy_connect_component is None:
        st.error("Esta implantação do Streamlit ainda não oferece Components v2. Atualize a versão do Streamlit antes de testar a conexão.")
    else:
        # Diagnóstico real da API: não dependemos da lista visual do Dashboard.
        if st.button("🧪 Verificar Pluggy Sandbox",use_container_width=True,key="pluggy_check_v64"):
            try:
                connector, rows = pluggy_bank_sandbox()
                st.session_state["_pluggy_v64_sandbox_checked"]=True
                st.session_state["_pluggy_v64_connector_id"]=connector.get("id") if connector else None
                st.session_state["_pluggy_v64_connector_name"]=(connector.get("name") if connector else None)
                st.session_state["_pluggy_v64_sandbox_names"]=[str(x.get("name") or "Conector sem nome") for x in rows]
            except Exception as e:
                st.session_state["_pluggy_v64_sandbox_checked"]=True
                st.session_state["_pluggy_v64_connector_id"]=None
                st.session_state["_pluggy_v64_sandbox_error"]=str(e)

        checked=st.session_state.get("_pluggy_v64_sandbox_checked",False)
        connector_id=st.session_state.get("_pluggy_v64_connector_id")
        sandbox_error=st.session_state.pop("_pluggy_v64_sandbox_error",None)
        if sandbox_error:
            st.error(f"A API da Pluggy não conseguiu listar o Sandbox: {sandbox_error}")
        elif checked and connector_id:
            st.success(f"🧪 Sandbox detectado pela API: Pluggy Bank (ID {connector_id}).")
        elif checked:
            names=st.session_state.get("_pluggy_v64_sandbox_names",[])
            st.error("A API autenticada não retornou o conector Pluggy Bank para esta aplicação.")
            if names:
                with st.expander("Ver conectores Sandbox retornados pela API"):
                    st.write(", ".join(names))
            st.caption("Nesse caso, não use banco real. O diagnóstico indica uma limitação/configuração da aplicação Pluggy, não do formulário do Meu Financeiro.")

        if connector_id:
            if st.button("🔐 Preparar Pluggy Bank Sandbox",use_container_width=True,key="pluggy_prepare_v64"):
                try:
                    st.session_state["_pluggy_v64_token"]=pluggy_connect_token()
                    st.success("Conexão segura preparada. Abra o Pluggy Bank no botão abaixo.")
                except Exception as e:
                    st.error(f"Não foi possível preparar a conexão: {e}")

            token=st.session_state.get("_pluggy_v64_token")
            if token:
                result=pluggy_connect_component(
                    data={"connectToken":token,"selectedConnectorId":connector_id},
                    default={"connected":None,"connect_error":None},
                    key="pluggy_connect_v64",
                    on_connected_change=lambda: None,
                    on_connect_error_change=lambda: None,
                )
                item_id=getattr(result,"connected",None)
                connect_error=getattr(result,"connect_error",None)
                if connect_error:
                    st.error(f"Pluggy Connect: {connect_error}")
                if item_id:
                    st.session_state["_pluggy_item_id"]=str(item_id)
                    st.success("✅ Pluggy Bank conectado. O Item ID foi recebido pelo Meu Financeiro.")

        saved_item=st.session_state.get("_pluggy_item_id")
        if saved_item:
            st.markdown("#### 🔄 Importar dados do Pluggy Bank")
            st.caption("A sincronização usa diretamente o Item recém-conectado e importa conta, saldo e novas movimentações sem duplicar transações já importadas.")
            if st.button("🔄 Sincronizar conta, saldo e movimentações",use_container_width=True,key="pluggy_sync_v64"):
                with st.spinner("Sincronizando dados da Pluggy..."):
                    try:
                        na,nt=sync_pluggy_item(saved_item)
                        st.success(f"Sincronização concluída: {na} conta(s) e {nt} nova(s) movimentação(ões).")
                        st.session_state["_pluggy_v64_token"]=None
                    except Exception as e:
                        st.error(f"Não foi possível sincronizar: {e}")

        with st.expander("🧪 Credenciais fictícias do Pluggy Bank"):
            st.code("Usuário: user-ok\nSenha: password-ok\nMFA (se pedir): 123456",language=None)
            st.caption("Use somente essas credenciais de teste. Não informe CPF, senha ou dados de banco real.")


elif page=="📱 WhatsApp":
    st.title("📱 WhatsApp do MEFI")
    st.caption("Vincule os números autorizados a usar o MEFI com esta conta do Meu Financeiro.")
    st.info("🔐 O número fica associado ao seu usuário autenticado. Nesta etapa ele é pré-vinculado; a confirmação de posse pelo próprio WhatsApp será ativada quando conectarmos o canal oficial do MEFI.")

    def _normalizar_whatsapp(valor):
        digitos=re.sub(r"\D","",str(valor or ""))
        if len(digitos) in (10,11):
            digitos="55"+digitos
        return digitos if len(digitos) in (12,13) and digitos.startswith("55") else ""

    def _mascarar_whatsapp(valor):
        d=re.sub(r"\D","",str(valor or ""))
        if d.startswith("55"): d=d[2:]
        if len(d)>=10:
            return f"({d[:2]}) *****-{d[-4:]}"
        return "Número protegido"

    try:
        vinculos=myrows("whatsapp_vinculos","created_at")
        tabela_whatsapp_ok=True
    except Exception:
        vinculos=[]; tabela_whatsapp_ok=False

    if not tabela_whatsapp_ok:
        st.warning("A estrutura do WhatsApp ainda precisa ser criada no Supabase. Execute o SQL da versão V69 uma única vez e recarregue esta página.")
    else:
        ativos=[x for x in vinculos if bool(x.get("ativo",True))]
        st.markdown("### Números autorizados")
        if not ativos:
            st.caption("Nenhum número foi vinculado ainda.")
        for x in ativos:
            c1,c2,c3=st.columns([2.2,1.5,1])
            nome=str(x.get("nome") or "Usuário")
            c1.markdown(f"**{nome}**  \n{_mascarar_whatsapp(x.get('telefone_e164'))}")
            if bool(x.get("verificado",False)):
                c2.success("✅ Verificado")
            else:
                c2.warning("🟡 Pré-vinculado")
                codigo=str(x.get("codigo_vinculo") or "")
                if codigo: c2.caption(f"Código: **{codigo}**")
            if c3.button("Desvincular",key=f"wa_del_{x['id']}",use_container_width=True):
                sb.table("whatsapp_vinculos").delete().eq("id",x["id"]).eq("user_id",st.session_state.uid).execute()
                st.cache_data.clear(); st.rerun()

        st.divider()
        st.markdown("### ➕ Vincular outro celular")
        with st.form("whatsapp_link_form",clear_on_submit=True):
            nome=st.text_input("Quem vai usar este WhatsApp?",placeholder="Ex.: Gabriel, Esposa")
            telefone=st.text_input("Número com DDD",placeholder="Ex.: (19) 99999-9999")
            autoriza=st.checkbox("Autorizo este número a ser vinculado à minha conta do Meu Financeiro.")
            salvar=st.form_submit_button("📱 Pré-vincular número",use_container_width=True,type="primary")
        if salvar:
            tel=_normalizar_whatsapp(telefone)
            if not nome.strip():
                st.warning("Informe quem usará este número.")
            elif not tel:
                st.warning("Informe um número brasileiro válido com DDD.")
            elif not autoriza:
                st.warning("Confirme a autorização antes de vincular.")
            else:
                codigo=f"{secrets.randbelow(1000000):06d}"
                try:
                    sb.table("whatsapp_vinculos").insert({
                        "user_id":st.session_state.uid,"nome":nome.strip(),"telefone_e164":"+"+tel,
                        "codigo_vinculo":codigo,"verificado":False,"ativo":True
                    }).execute()
                    st.cache_data.clear()
                    st.success("Número pré-vinculado à sua conta. Quando o canal oficial do MEFI estiver ativo, este código servirá para confirmar que a mensagem veio do celular autorizado.")
                    st.rerun()
                except Exception:
                    st.error("Não foi possível vincular. Esse número pode já estar associado a outra conta.")

        st.divider()
        st.markdown("### Como funcionará no MEFI")
        st.write("Depois da ativação do canal oficial, o MEFI receberá o número do remetente, localizará o vínculo correspondente e trabalhará somente com a conta autorizada. Lançamentos financeiros continuarão pedindo confirmação antes de serem gravados.")
        st.caption("A pré-vinculação não envia mensagens e não ativa cobrança de WhatsApp. Nenhuma senha bancária é solicitada.")

elif page=="🤖 MEFI":
    st.title("🤖 MEFI — Assistente Inteligente")
    st.caption("Converse sobre seus próprios dados financeiros.")
    st.caption("O MEFI analisa informações da sua conta, mas não movimenta dinheiro sem confirmação e não acessa senhas ou dados completos de cartão.")

    # Monta um resumo financeiro limitado ao usuário autenticado.
    t=date.today();ini=t.replace(day=1);fim=date(t.year,t.month,calendar.monthrange(t.year,t.month)[1])
    mov=pd.DataFrame(myrows("mov","data"))
    if len(mov):
        mov["data"]=pd.to_datetime(mov["data"]).dt.date
        mm=mov[(mov.data>=ini)&(mov.data<=fim)].copy()
    else:mm=pd.DataFrame()

    gastos=float(mm.loc[mm.tipo=="Saída","valor"].sum()) if len(mm) else 0
    extras=float(mm.loc[mm.tipo=="Entrada","valor"].sum()) if len(mm) else 0
    renda_base=family_income_total(cfg)
    guardar=renda_base*float(cfg["pct"])/100
    contas=myrows("contas")
    cards=myrows("cartoes")
    dividas=myrows("dividas")
    metas=myrows("metas")
    pend=sum(float(x["valor"]) for x in contas if x["status"]!="Pago")
    fatura=sum(float(x["fatura"]) for x in cards)
    recorrentes,rec_total,rec_pendente=recurring_summary()
    _,parc_pendente=installment_summary()
    # O saldo informado à IA deve refletir contas, recorrentes e parcelamentos ainda pendentes.
    saldo=max(renda_base+extras-gastos-guardar-pend-rec_pendente-parc_pendente-fatura,0)

    categorias={}
    recentes=[]
    if len(mm):
        saidas=mm[mm.tipo=="Saída"]
        if len(saidas):
            categorias={str(k):float(v) for k,v in saidas.groupby("categoria")["valor"].sum().items()}
        cols=[c for c in ["data","descricao","categoria","tipo","forma","valor"] if c in mm.columns]
        recentes=mm.sort_values("data",ascending=False)[cols].head(20).copy()
        if len(recentes):
            recentes["data"]=recentes["data"].astype(str)
            recentes=recentes.to_dict("records")

    resumo={
        "mes":t.strftime("%Y-%m"),
        "renda_base":renda_base,
        "entradas_extras":extras,
        "gastos_registrados":gastos,
        "meta_guardar":guardar,
        "saldo_estimado_para_uso":saldo,
        "contas_pendentes":pend,
        "faturas_cadastradas":fatura,
        "recorrentes_mensais":rec_total,
        "recorrentes_pendentes_no_mes":rec_pendente,
        "recorrentes":[{"nome":x.get("nome"),"categoria":x.get("categoria"),"valor":x.get("valor"),"dia_vencimento":x.get("dia_vencimento"),"pago_no_mes":x.get("ultimo_pago_mes")==t.strftime("%Y-%m")} for x in recorrentes],
        "reserva_minima":float(cfg["reserva"]),
        "gastos_por_categoria":categorias,
        "dividas":[{"nome":x.get("nome"),"saldo":x.get("saldo"),"parcela":x.get("parcela"),"restantes":x.get("restantes")} for x in dividas],
        "metas":[{"nome":x.get("nome"),"desejado":x.get("desejado"),"guardado":x.get("guardado")} for x in metas],
        "movimentacoes_recentes":recentes
    }

    a,b,c,d=st.columns(4)
    a.metric("💰 Renda base",money(renda_base))
    b.metric("📤 Gastos do mês",money(gastos))
    c.metric("🧾 Contas + compromissos",money(pend+rec_pendente+parc_pendente))
    d.metric("💳 Faturas",money(fatura))
    if rec_total>0: st.caption(f"🔁 Recorrentes cadastrados: {money(rec_total)}/mês • ainda pendentes neste mês: {money(rec_pendente)}")

    st.info("🔒 Para responder, o MEFI recebe apenas um resumo dos seus valores financeiros. Descrições individuais das suas movimentações não são enviadas. Nunca informe senha bancária, CVV ou número completo de cartão no chat.")

    with st.expander("🌐📱💬 Onde o MEFI vai funcionar"):
        st.write("**Site:** ativo nesta versão.  **Aplicativo:** planejado usando a mesma conta.  **WhatsApp:** integração em preparação.")
        st.caption("No WhatsApp, o número será apenas um identificador previamente autorizado e vinculado ao user_id da conta. Nenhum número terá acesso aos dados financeiros sem vinculação segura feita pelo usuário.")

    def resposta_financeira_local(pergunta_local):
        """Fallback local: responde perguntas financeiras comuns sem depender do Gemini."""
        q=(pergunta_local or "").lower()
        # Mesma regra da Home: limite seguro considera a reserva mínima e os dias até o próximo recebimento.
        nd_ia,_,_=next_family_income(cfg)
        dias_restantes=max((nd_ia-t).days,1)
        limite_dia=max(saldo-float(cfg["reserva"]),0)/dias_restantes
        maior_cat=max(categorias.items(), key=lambda kv: kv[1]) if categorias else None

        if any(x in q for x in ["quanto posso gastar","quanto posso usar","posso gastar","disponível","disponivel"]):
            return (f"Você pode usar até {money(saldo)} dentro do planejamento atual. "
                    f"Esse valor já considera {money(gastos)} em gastos realizados, {money(guardar)} para guardar, "
                    f"{money(pend)} em contas pendentes, {money(rec_pendente)} em recorrentes ainda pendentes e {money(fatura)} em faturas cadastradas. "
                    f"Seu limite seguro é de aproximadamente {money(limite_dia)} por dia até o próximo recebimento.")

        if any(x in q for x in ["onde gasto","onde estou gastando","gasto mais","categoria"]):
            if maior_cat:
                cat,val=maior_cat
                return (f"Sua maior categoria de gastos neste mês é {cat}, com {money(val)}. "
                        f"Seus gastos realizados somam {money(gastos)} no mês.")
            return "Ainda não há gastos por categoria suficientes para eu comparar neste mês."

        if any(x in q for x in ["como está meu mês","como esta meu mes","situação","situacao","resumo do mês","resumo do mes"]):
            return (f"Neste mês, sua renda base é {money(renda_base)}, você já gastou {money(gastos)} e separou "
                    f"{money(guardar)} para guardar. Há {money(pend+rec_pendente)} em compromissos ainda pendentes. "
                    f"Seu saldo estimado para uso é {money(saldo)}.")

        if "recorrent" in q or "assinatura" in q:
            return (f"Você tem {money(rec_total)} por mês em gastos recorrentes cadastrados. "
                    f"Neste mês, {money(rec_pendente)} ainda está pendente.")

        if "dívida" in q or "divida" in q:
            total_div=sum(float(x.get("saldo") or 0) for x in dividas)
            return f"O saldo total das dívidas cadastradas é {money(total_div)}."

        if "meta" in q or "guardar" in q or "econom" in q:
            return (f"Sua meta atual de guardar no mês é {money(guardar)}. "
                    f"Depois dos gastos e compromissos pendentes, o saldo estimado para uso é {money(saldo)}.")

        return (f"No momento, seu resumo é: renda base {money(renda_base)}, gastos {money(gastos)}, "
                f"compromissos pendentes {money(pend+rec_pendente)} e saldo estimado para uso {money(saldo)}. "
                "Você pode perguntar, por exemplo, quanto pode gastar, onde está gastando mais, como está o mês, metas, dívidas ou recorrentes.")

    sugestoes=st.columns(3)
    if sugestoes[0].button("💸 Quanto posso gastar?",use_container_width=True):
        st.session_state.ai_history=[]
        st.session_state.ai_quick=True
        st.session_state.ai_question="Quanto posso gastar até o fim deste mês sem comprometer minha meta de guardar e minha reserva?"
    if sugestoes[1].button("📊 Onde gasto mais?",use_container_width=True):
        st.session_state.ai_history=[]
        st.session_state.ai_quick=True
        st.session_state.ai_question="Analise onde estou gastando mais neste mês e explique de forma curta."
    if sugestoes[2].button("🔮 Como está meu mês?",use_container_width=True):
        st.session_state.ai_history=[]
        st.session_state.ai_quick=True
        st.session_state.ai_question="Faça um resumo da minha situação financeira neste mês e destaque os pontos que merecem atenção."

    # V70 — a mesma conversa do MEFI agora também registra entradas e saídas.
    # O lançamento só é gravado depois de confirmação explícita do usuário.
    if "ai_history" not in st.session_state:
        st.session_state.ai_history=[]
    if "mefi_lancamento_pendente" not in st.session_state:
        st.session_state.mefi_lancamento_pendente=None

    for item in st.session_state.ai_history[-12:]:
        with st.chat_message(item["role"]):
            st.markdown(item["content"])

    pendente_chat=st.session_state.get("mefi_lancamento_pendente")
    if pendente_chat:
        icone="🟢" if pendente_chat["tipo"]=="Entrada" else "🔴"
        data_txt=pendente_chat["data"].strftime("%d/%m/%Y") if hasattr(pendente_chat["data"],"strftime") else str(pendente_chat["data"])
        resumo_pendente=(
            f"{icone} **{pendente_chat['tipo']} identificada**  \n"
            f"**{pendente_chat['descricao']} — {money(pendente_chat['valor'])}**  \n"
            f"📂 {pendente_chat['categoria']} · 💳 {pendente_chat['forma']} · 📅 {data_txt}"
        )
        with st.chat_message("assistant"):
            st.markdown(resumo_pendente)
            st.caption("Confira os dados antes de registrar.")
            b1,b2,b3=st.columns(3)
            confirmar=b1.button("✅ Confirmar",use_container_width=True,key="mefi_confirmar_lancamento",type="primary")
            corrigir=b2.button("✏️ Corrigir",use_container_width=True,key="mefi_corrigir_lancamento")
            cancelar=b3.button("❌ Cancelar",use_container_width=True,key="mefi_cancelar_lancamento")

        if confirmar:
            try:
                sb.table("mov").insert({
                    "user_id":st.session_state.uid,
                    "data":pendente_chat["data"].isoformat() if hasattr(pendente_chat["data"],"isoformat") else str(pendente_chat["data"]),
                    "descricao":pendente_chat["descricao"],
                    "categoria":pendente_chat["categoria"],
                    "tipo":pendente_chat["tipo"],
                    "forma":pendente_chat["forma"],
                    "valor":float(pendente_chat["valor"]),
                    "obs":"Lançado pela conversa do MEFI"
                }).execute()
                st.cache_data.clear()
                msg=f"✅ Registrado! **{pendente_chat['descricao']} — {money(pendente_chat['valor'])}** foi adicionado às suas movimentações."
                st.session_state.ai_history.append({"role":"assistant","content":msg})
                st.session_state.mefi_lancamento_pendente=None
                st.rerun()
            except Exception as e:
                st.error(f"Não consegui registrar o lançamento agora: {e}")

        if corrigir:
            st.session_state.mefi_lancamento_pendente=None
            st.session_state.ai_history.append({"role":"assistant","content":"✏️ Certo. Envie novamente a movimentação com os dados corrigidos."})
            st.rerun()

        if cancelar:
            st.session_state.mefi_lancamento_pendente=None
            st.session_state.ai_history.append({"role":"assistant","content":"❌ Lançamento cancelado. Nada foi registrado."})
            st.rerun()

    pergunta=st.chat_input(
        "Confirme, corrija ou cancele o lançamento acima antes de continuar." if pendente_chat
        else "Ex.: Gastei 80 de gasolina no Pix — ou pergunte algo sobre suas finanças...",
        disabled=bool(pendente_chat)
    )
    if pergunta:
        st.session_state.ai_quick=False
        st.session_state.ai_question=pergunta

    pergunta_atual=st.session_state.pop("ai_question",None)
    if pergunta_atual:
        with st.chat_message("user"):
            st.markdown(pergunta_atual)

        # Primeiro verifica se a mensagem parece ser um lançamento financeiro.
        norm_lanc=unicodedata.normalize("NFKD",pergunta_atual.lower())
        norm_lanc="".join(c for c in norm_lanc if not unicodedata.combining(c))
        verbos_lanc=["gastei","paguei","comprei","abasteci","custou","saiu","recebi","ganhei","vendi","faturei","entrou","caiu","depositaram","me pagaram","coloquei gasolina"]
        parece_lancamento=any(v in norm_lanc for v in verbos_lanc)
        interpretado=interpretar_lancamento(pergunta_atual) if parece_lancamento else None

        if interpretado and float(interpretado.get("valor") or 0)>0:
            st.session_state.ai_history.append({"role":"user","content":pergunta_atual})
            st.session_state.mefi_lancamento_pendente=interpretado
            st.rerun()

        # Se não for lançamento, mantém o Assistente Inteligente que já existia.
        pergunta_rapida=bool(st.session_state.pop("ai_quick",False))
        api_key=st.secrets.get("GEMINI_API_KEY",None)

        if pergunta_rapida:
            resposta=resposta_financeira_local(pergunta_atual)
        elif parece_lancamento and (not interpretado or float(interpretado.get("valor") or 0)<=0):
            resposta="🤖 Entendi que você quer registrar uma movimentação, mas não consegui identificar um valor. Tente, por exemplo: **Gastei 80 de gasolina no Pix**."
        elif not api_key:
            resposta=resposta_financeira_local(pergunta_atual)
        else:
            try:
                from google import genai
                from google.genai import types
                client=genai.Client(api_key=api_key,http_options=types.HttpOptions(timeout=15000))
                instrucoes="""Você é o MEFI — Assistente Inteligente do aplicativo Meu Financeiro.
Responda sempre em português do Brasil, de forma clara, curta e prática.
Use SOMENTE os dados financeiros fornecidos no contexto. Se faltar informação, diga que não há dados suficientes.
Não invente valores. Não prometa retornos. Não faça movimentações financeiras.
Quando houver cálculo, explique o resultado de forma simples.
Trate projeções como estimativas, não garantias.
Nunca peça senha bancária, CVV, número completo de cartão ou credenciais.
Ajude o usuário a entender opções e consequências, preservando a decisão final dele.
Use Markdown simples e não coloque valores monetários entre crases ou blocos de código."""
                resumo_ia={k:v for k,v in resumo.items() if k!="movimentacoes_recentes"}
                contexto="DADOS FINANCEIROS RESUMIDOS DO USUÁRIO:\n"+json.dumps(resumo_ia,ensure_ascii=False,default=str)
                modelos=["gemini-3.8-flash","gemini-3.6-flash","gemini-3.5-flash-lite"]
                resposta=None
                for modelo in modelos:
                    try:
                        resp=client.models.generate_content(
                            model=modelo,
                            contents=contexto+"\n\nPERGUNTA DO USUÁRIO:\n"+pergunta_atual,
                            config=types.GenerateContentConfig(system_instruction=instrucoes,max_output_tokens=700)
                        )
                        if getattr(resp,"text",None):
                            resposta=resp.text
                            break
                    except Exception:
                        continue
                if not resposta:
                    resposta=resposta_financeira_local(pergunta_atual)
            except Exception:
                resposta=resposta_financeira_local(pergunta_atual)

        resposta=resposta.replace("```markdown","").replace("```","").strip()
        with st.chat_message("assistant"):
            st.markdown(resposta)
        st.session_state.ai_history.append({"role":"user","content":pergunta_atual})
        st.session_state.ai_history.append({"role":"assistant","content":resposta})

    if st.button("🧹 Limpar conversa"):
        st.session_state.ai_history=[]
        st.rerun()

elif page=="🚦 Saúde Financeira":
    st.title("🚦 Saúde Financeira")
    st.caption("Entenda rapidamente como está sua situação financeira com base nos dados cadastrados.")

    t=date.today();ini=t.replace(day=1);fim=date(t.year,t.month,calendar.monthrange(t.year,t.month)[1])
    mov=pd.DataFrame(myrows("mov","data"))
    if len(mov):
        mov["data"]=pd.to_datetime(mov["data"]).dt.date
        mm=mov[(mov.data>=ini)&(mov.data<=fim)]
    else:mm=pd.DataFrame()

    gastos=float(mm.loc[mm.tipo=="Saída","valor"].sum()) if len(mm) else 0
    extras=float(mm.loc[mm.tipo=="Entrada","valor"].sum()) if len(mm) else 0
    renda_base=family_income_total(cfg)
    renda=renda_base+extras
    guardar=renda_base*float(cfg["pct"])/100
    contas=myrows("contas");pend=sum(float(x["valor"]) for x in contas if x["status"]!="Pago")
    cards=myrows("cartoes");fatura=sum(float(x["fatura"]) for x in cards)
    _,rec_total,rec_pendente=recurring_summary()
    _,parc_pendente=installment_summary()
    compromissos=gastos+pend+fatura+rec_pendente+parc_pendente
    livre=renda-guardar-compromissos
    taxa=(compromissos/renda*100) if renda>0 else 0

    if renda<=0:
        status="⚪ Aguardando dados";msg="Configure sua renda para liberar sua análise financeira."
    elif taxa<=60 and livre>float(cfg["reserva"]):
        status="🟢 Tranquilo";msg="Seus compromissos estão dentro de uma faixa confortável para os dados cadastrados."
    elif taxa<=85:
        status="🟡 Atenção";msg="Uma parte importante da sua renda já está comprometida. Acompanhe os próximos gastos."
    else:
        status="🔴 Orçamento apertado";msg="Seus gastos, contas e faturas estão consumindo grande parte da renda cadastrada."

    st.subheader(status);st.write(msg)
    a,b,c,d=st.columns(4)
    a.metric("💰 Renda do mês",money(renda))
    b.metric("📤 Compromissos",money(compromissos))
    c.metric("🐷 Meta para guardar",money(guardar))
    d.metric("💵 Livre projetado",money(livre))
    st.progress(min(max(taxa/100,0),1),text=f"{taxa:.0f}% da renda comprometida")

    st.subheader("🧠 Leitura rápida")
    st.write(f"**Gastos registrados:** {money(gastos)}")
    st.write(f"**Contas pendentes:** {money(pend)}")
    st.write(f"**Faturas cadastradas:** {money(fatura)}")
    st.write(f"**Recorrentes ainda pendentes no mês:** {money(rec_pendente)}")
    st.write(f"**Parcelas ainda pendentes no mês:** {money(parc_pendente)}")
    if renda>0:
        if livre>float(cfg["reserva"]): st.success(f"Após compromissos e sua meta de economia, a projeção livre é de {money(livre)}.")
        elif livre>0: st.warning(f"A projeção livre é de {money(livre)}, próxima ou abaixo da sua reserva mínima.")
        else: st.error(f"Os compromissos atuais ultrapassam o valor disponível em aproximadamente {money(abs(livre))}.")

elif page=="🔮 Previsão":
    st.title("🔮 Previsão Financeira")
    st.caption("Estimativas baseadas nos dados cadastrados e no ritmo de gastos deste mês.")

    t=date.today();ini=t.replace(day=1);dias_mes=calendar.monthrange(t.year,t.month)[1]
    mov=pd.DataFrame(myrows("mov","data"))
    if len(mov):
        mov["data"]=pd.to_datetime(mov["data"]).dt.date
        mm=mov[(mov.data>=ini)&(mov.data<=t)]
    else:mm=pd.DataFrame()

    gastos=float(mm.loc[mm.tipo=="Saída","valor"].sum()) if len(mm) else 0
    extras=float(mm.loc[mm.tipo=="Entrada","valor"].sum()) if len(mm) else 0
    renda=family_income_total(cfg)
    media_dia=gastos/max(t.day,1)
    gasto_estimado=media_dia*dias_mes
    guardar=renda*float(cfg["pct"])/100
    contas=myrows("contas");pend=sum(float(x["valor"]) for x in contas if x["status"]!="Pago")
    cards=myrows("cartoes");fatura=sum(float(x["fatura"]) for x in cards)
    _,rec_total,rec_pendente=recurring_summary()
    _,parc_pendente=installment_summary()
    base=renda+extras-pend-rec_pendente-parc_pendente-fatura-guardar
    projecao=base-gasto_estimado
    faltam=max(dias_mes-t.day,0)

    a,b,c,d=st.columns(4)
    a.metric("📆 Gasto médio/dia",money(media_dia))
    b.metric("📤 Gasto estimado no mês",money(gasto_estimado))
    c.metric("🧾 Compromissos pendentes",money(pend+rec_pendente+parc_pendente+fatura))
    d.metric("🔮 Saldo projetado",money(projecao))

    st.subheader("Até o fim do mês")
    st.write(f"Faltam **{faltam} dia(s)** para terminar o mês.")
    if renda<=0:
        st.info("Configure sua renda para liberar uma previsão mais completa.")
    elif projecao>float(cfg["reserva"]):
        st.success(f"Mantendo o ritmo atual, a estimativa é terminar o mês com aproximadamente **{money(projecao)}**.")
    elif projecao>0:
        st.warning(f"A estimativa termina positiva em **{money(projecao)}**, mas próxima ou abaixo da reserva mínima.")
    else:
        st.error(f"No ritmo atual, a estimativa indica um déficit aproximado de **{money(abs(projecao))}**.")

    st.subheader("📊 Simulação de cenários")
    cen=pd.DataFrame({
        "Cenário":["Economizando 15%","Ritmo atual","Gastando 15% a mais"],
        "Saldo projetado":[base-(media_dia*.85*dias_mes),base-gasto_estimado,base-(media_dia*1.15*dias_mes)]
    })
    cen["Saldo projetado"]=cen["Saldo projetado"].apply(money)
    st.dataframe(cen,use_container_width=True,hide_index=True)
    st.caption("As previsões são estimativas e mudam conforme você registra novas entradas, gastos e contas.")

elif page=="➕ Registrar":
    if st.button("← Voltar ao Início",key="voltar_inicio_registrar"):
        st.session_state["_page_destino"]="🏠 Início"
        st.rerun()
    # Limpa a descrição somente no início de uma nova execução.
    # Isso evita alterar a chave de um widget depois que ele já foi instanciado.
    if st.session_state.pop("_limpar_mov_desc", False):
        st.session_state["mov_desc"] = ""
    st.title("➕ Registrar")
    st.caption("Digite a descrição e o Meu Financeiro sugere uma categoria automaticamente. Você continua no controle e pode alterá-la antes de salvar.")
    desc=st.text_input("Descrição",key="mov_desc",placeholder="Ex.: Uber, iFood, Netflix, Farmácia")
    categorias=["Moradia","Alimentação","Transporte","Saúde","Lazer","Assinaturas","Compras","Outros"]
    sugerida=sugerir_categoria(desc)
    if desc.strip():
        aprendida=categoria_aprendida(desc)
        if aprendida:
            st.success(f"🧠 Aprendi com seus lançamentos: **{aprendida}**")
        else:
            st.info(f"✨ Categoria sugerida: **{sugerida}**")
    idx=categorias.index(sugerida) if sugerida in categorias else len(categorias)-1
    texto_desc=normalizar_descricao(desc)
    termos_recorrentes=["netflix","spotify","internet","celular","telefone","academia","aluguel","condominio","icloud","google one","prime","disney","hbo","max","deezer","seguro","mensalidade","assinatura"]
    repeticoes=sum(1 for m in myrows("mov","data") if normalizar_descricao(m.get("descricao",""))==texto_desc) if texto_desc else 0
    parece_recorrente=bool(texto_desc) and (repeticoes>=2 or any(t in texto_desc for t in termos_recorrentes))
    if parece_recorrente:
        st.warning("🔁 Este lançamento parece recorrente. Você pode cadastrá-lo automaticamente como compromisso mensal ao salvar.")
    with st.form("mov",clear_on_submit=True):
        dt=st.date_input("Data",date.today())
        a,b=st.columns(2)
        cat=a.selectbox("Categoria",categorias,index=idx)
        tipo=b.selectbox("Tipo",["Saída","Entrada"])
        a,b=st.columns(2)
        forma=a.selectbox("Forma",["Pix","Débito","Crédito","Dinheiro","Outro"])
        valor=b.number_input("Valor",min_value=0.0)
        obs=st.text_input("Observação")
        tornar_recorrente=st.checkbox("🔁 Também cadastrar como gasto recorrente",value=False,disabled=(tipo!="Saída"))
        dia_rec=st.number_input("Dia do vencimento do recorrente",1,28,int(dt.day if dt.day<=28 else 28),disabled=not tornar_recorrente)
        salvar=st.form_submit_button("💾 Salvar",use_container_width=True)
    if salvar:
        if not desc.strip() or valor<=0:
            st.warning("Informe uma descrição e um valor maior que zero.")
        else:
            sb.table("mov").insert({"user_id":st.session_state.uid,"data":dt.isoformat(),"descricao":desc.strip(),"categoria":cat,"tipo":tipo,"forma":forma,"valor":valor,"obs":obs}).execute()
            if tornar_recorrente and tipo=="Saída":
                existentes=myrows("recorrentes")
                ja_existe=any(normalizar_descricao(x.get("nome",""))==normalizar_descricao(desc) and x.get("ativo",True) for x in existentes)
                if not ja_existe:
                    sb.table("recorrentes").insert({"user_id":st.session_state.uid,"nome":desc.strip(),"categoria":cat,"valor":valor,"dia_vencimento":int(dia_rec),"forma":forma,"ativo":True,"ultimo_pago_mes":date.today().strftime("%Y-%m")}).execute()
                    st.success("Lançamento salvo e recorrente criado. Este mês já foi marcado como pago para não descontar duas vezes.")
                else:
                    st.info("Lançamento salvo. Já existe um recorrente ativo com esse nome, então não criei outro.")
            else:
                st.success("Lançamento salvo. Sua escolha de categoria será reaproveitada quando a mesma descrição aparecer novamente.")
            st.session_state["_limpar_mov_desc"] = True
            st.rerun()
    movimentos = myrows("mov","data")
    st.dataframe(pd.DataFrame(movimentos),use_container_width=True,hide_index=True)

    if movimentos:
        st.subheader("✏️ Gerenciar lançamentos")
        st.caption("Edite um lançamento incorreto ou exclua-o com confirmação. As alterações atualizam os cálculos do sistema.")
        for m in movimentos:
            mid = m.get("id")
            titulo = f"{m.get('descricao','Sem descrição')} — {money(m.get('valor',0))} — {m.get('data','')}"
            with st.expander(titulo):
                with st.form(f"editar_mov_{mid}"):
                    e_desc = st.text_input("Descrição", value=str(m.get("descricao", "")), key=f"ed_desc_{mid}")
                    e_cat = st.selectbox("Categoria", categorias, index=(categorias.index(m.get("categoria")) if m.get("categoria") in categorias else len(categorias)-1), key=f"ed_cat_{mid}")
                    e_tipo = st.selectbox("Tipo", ["Saída","Entrada"], index=(0 if m.get("tipo") != "Entrada" else 1), key=f"ed_tipo_{mid}")
                    e_forma_opts=["Pix","Débito","Crédito","Dinheiro","Outro"]
                    e_forma = st.selectbox("Forma", e_forma_opts, index=(e_forma_opts.index(m.get("forma")) if m.get("forma") in e_forma_opts else 4), key=f"ed_forma_{mid}")
                    e_valor = st.number_input("Valor", min_value=0.0, value=float(m.get("valor") or 0), step=1.0, key=f"ed_valor_{mid}")
                    e_obs = st.text_input("Observação", value=str(m.get("obs") or ""), key=f"ed_obs_{mid}")
                    if st.form_submit_button("💾 Salvar alterações", use_container_width=True):
                        if not e_desc.strip() or e_valor <= 0:
                            st.warning("Informe uma descrição e um valor maior que zero.")
                        else:
                            sb.table("mov").update({"descricao":e_desc.strip(),"categoria":e_cat,"tipo":e_tipo,"forma":e_forma,"valor":e_valor,"obs":e_obs}).eq("id",mid).eq("user_id",st.session_state.uid).execute()
                            st.success("Lançamento atualizado.")
                            st.rerun()
                confirmar = st.checkbox("Confirmo que quero excluir este lançamento", key=f"conf_del_{mid}")
                if st.button("🗑️ Excluir lançamento", key=f"del_mov_{mid}", disabled=not confirmar, use_container_width=True):
                    sb.table("mov").delete().eq("id",mid).eq("user_id",st.session_state.uid).execute()
                    st.success("Lançamento excluído.")
                    st.rerun()

elif page=="🔁 Recorrentes":
    st.title("🔁 Contas que se repetem todo mês")
    st.caption("Use para internet, academia, assinaturas e outros gastos que voltam todos os meses.")
    if st.button("← Voltar ao Início",key="voltar_inicio_rec"):
        st.session_state["_page_destino"]="🏠 Início"; st.rerun()
    mes_atual=date.today().strftime("%Y-%m")
    with st.form("recorrente",clear_on_submit=True):
        n=st.text_input("Nome",placeholder="Ex.: Internet, Netflix, Academia")
        a,b=st.columns(2)
        cat=a.selectbox("Categoria",["Moradia","Alimentação","Transporte","Saúde","Lazer","Assinaturas","Compras","Outros"],key="rec_cat")
        valor=b.number_input("Valor mensal",min_value=0.0,step=1.0)
        a,b=st.columns(2)
        dia=a.number_input("Dia do vencimento",1,28,10)
        forma=b.selectbox("Forma de pagamento",["Pix","Débito","Crédito","Dinheiro","Boleto","Outro"],key="rec_forma")
        if st.form_submit_button("➕ Adicionar recorrente",use_container_width=True):
            if not n.strip() or valor<=0:
                st.warning("Informe o nome e um valor maior que zero.")
            else:
                sb.table("recorrentes").insert({"user_id":st.session_state.uid,"nome":n.strip(),"categoria":cat,"valor":valor,"dia_vencimento":int(dia),"forma":forma,"ativo":True,"ultimo_pago_mes":None}).execute()
                st.success("Gasto recorrente adicionado.");st.rerun()

    itens,total,pendente=recurring_summary()
    a,b,c=st.columns(3);a.metric("🔁 Total mensal",money(total));b.metric("⏳ Pendente neste mês",money(pendente));c.metric("✅ Já considerado pago",money(max(total-pendente,0)))
    if itens:
        st.subheader("Seus recorrentes")
        for x in itens:
            pago=x.get("ultimo_pago_mes")==mes_atual
            c1,c2,c3,c4=st.columns([4,2,2,2])
            c1.write(f"**{x.get('nome','')}**  ·  {x.get('categoria','')}")
            c2.write(money(x.get("valor",0)))
            c3.write(f"Dia {x.get('dia_vencimento','-')}")
            if pago:
                if c4.button("↩️ Desmarcar",key=f"unpay_{x['id']}",use_container_width=True):
                    sb.table("recorrentes").update({"ultimo_pago_mes":None}).eq("id",x["id"]).eq("user_id",st.session_state.uid).execute();st.rerun()
            else:
                if c4.button("✅ Pago no mês",key=f"pay_{x['id']}",use_container_width=True):
                    sb.table("recorrentes").update({"ultimo_pago_mes":mes_atual}).eq("id",x["id"]).eq("user_id",st.session_state.uid).execute();st.rerun()
            with st.expander(f"Editar / desativar — {x.get('nome','')}"):
                novo_valor=st.number_input("Valor",min_value=0.0,value=float(x.get("valor") or 0),key=f"rv_{x['id']}")
                novo_dia=st.number_input("Vencimento",1,28,int(x.get("dia_vencimento") or 10),key=f"rd_{x['id']}")
                e1,e2=st.columns(2)
                if e1.button("💾 Salvar",key=f"rs_{x['id']}",use_container_width=True):
                    sb.table("recorrentes").update({"valor":novo_valor,"dia_vencimento":int(novo_dia)}).eq("id",x["id"]).eq("user_id",st.session_state.uid).execute();st.rerun()
                if e2.button("🗑️ Desativar",key=f"rx_{x['id']}",use_container_width=True):
                    sb.table("recorrentes").update({"ativo":False}).eq("id",x["id"]).eq("user_id",st.session_state.uid).execute();st.rerun()
    else:
        st.info("Nenhum gasto recorrente cadastrado ainda.")

elif page=="💳 Parcelamentos":
    st.title("💳 Minhas compras parceladas")
    st.caption("Veja quanto falta pagar e marque as parcelas conforme forem sendo pagas.")
    if st.button("← Voltar ao Início",key="voltar_inicio_parc"):
        st.session_state["_page_destino"]="🏠 Início"; st.rerun()
    with st.form("novo_parcelamento",clear_on_submit=True):
        nome=st.text_input("Compra",placeholder="Ex.: iPhone, TV, Notebook")
        a,b=st.columns(2)
        valor=a.number_input("Valor da parcela",min_value=0.0,step=10.0)
        total=b.number_input("Total de parcelas",min_value=1,max_value=120,value=10,step=1)
        a,b=st.columns(2)
        pagas=a.number_input("Parcelas já pagas",min_value=0,max_value=120,value=0,step=1)
        dia=b.number_input("Dia do vencimento",min_value=1,max_value=31,value=10,step=1)
        a,b=st.columns(2)
        categoria=a.selectbox("Categoria",["Compras","Moradia","Transporte","Saúde","Lazer","Outros"])
        forma=b.selectbox("Forma de pagamento",["Crédito","Pix","Boleto","Débito","Outros"])
        if st.form_submit_button("➕ Adicionar parcelamento",use_container_width=True):
            if not nome.strip() or valor<=0 or int(pagas)>=int(total):
                st.error("Informe a compra, um valor maior que zero e deixe pelo menos uma parcela restante.")
            else:
                sb.table("parcelamentos").insert({"user_id":st.session_state.uid,"nome":nome.strip(),"categoria":categoria,"valor_parcela":float(valor),"total_parcelas":int(total),"parcelas_pagas":int(pagas),"dia_vencimento":int(dia),"forma":forma,"ativo":True,"ultimo_pago_mes":None}).execute(); st.rerun()
    itens,pendente=installment_summary()
    total_restante=sum(float(x.get("valor_parcela") or 0)*(int(x.get("total_parcelas") or 0)-int(x.get("parcelas_pagas") or 0)) for x in itens)
    a,b,c=st.columns(3);a.metric("Parcelamentos ativos",len(itens));b.metric("Pendente neste mês",money(pendente));c.metric("Saldo parcelado restante",money(total_restante))
    st.subheader("Seus parcelamentos")
    mes=date.today().strftime("%Y-%m")
    for x in itens:
        total=int(x.get("total_parcelas") or 0); pagas=int(x.get("parcelas_pagas") or 0); vp=float(x.get("valor_parcela") or 0)
        atual=min(pagas+1,total); restante=max(total-pagas,0); pct=(pagas/total) if total else 0
        st.markdown(f"**{x.get('nome')}** · {money(vp)} por parcela · **{pagas}/{total} pagas** · faltam **{restante}** · restante {money(vp*restante)}")
        st.progress(min(max(pct,0),1),text=f"{pct*100:.0f}% concluído")
        a,b=st.columns([3,1])
        a.caption(f"Próxima: parcela {atual}/{total} · dia {int(x.get('dia_vencimento') or 1)} · {x.get('forma') or 'Outros'}")
        pago=x.get("ultimo_pago_mes")==mes
        if not pago:
            if b.button("✅ Pagar parcela do mês",key=f"parc_pay_{x['id']}",use_container_width=True):
                novas_pagas=min(pagas+1,total)
                dados_up={"ultimo_pago_mes":mes,"parcelas_pagas":novas_pagas}
                if novas_pagas>=total: dados_up["ativo"]=False
                sb.table("parcelamentos").update(dados_up).eq("id",x["id"]).eq("user_id",st.session_state.uid).execute(); sync_installment_payments(); st.rerun()
        else:
            if b.button("↩️ Desmarcar",key=f"parc_unpay_{x['id']}",use_container_width=True):
                sb.table("parcelamentos").update({"ultimo_pago_mes":None,"parcelas_pagas":max(pagas-1,0)}).eq("id",x["id"]).eq("user_id",st.session_state.uid).execute(); sync_installment_payments(); st.rerun()
        with st.expander(f"Editar / desativar — {x.get('nome')}"):
            novo_nome=st.text_input("Nome",value=str(x.get("nome") or ""),key=f"pn_{x['id']}")
            novo_valor=st.number_input("Valor da parcela",min_value=0.0,value=vp,key=f"pv_{x['id']}")
            novo_dia=st.number_input("Dia do vencimento",min_value=1,max_value=31,value=int(x.get("dia_vencimento") or 1),key=f"pd_{x['id']}")
            if st.button("💾 Salvar alterações",key=f"ps_{x['id']}"):
                sb.table("parcelamentos").update({"nome":novo_nome,"valor_parcela":float(novo_valor),"dia_vencimento":int(novo_dia)}).eq("id",x["id"]).eq("user_id",st.session_state.uid).execute(); st.rerun()
            if st.button("🗑️ Desativar parcelamento",key=f"px_{x['id']}"):
                sb.table("parcelamentos").update({"ativo":False}).eq("id",x["id"]).eq("user_id",st.session_state.uid).execute(); st.rerun()
        st.divider()
    if not itens: st.info("Nenhum parcelamento ativo cadastrado.")

elif page=="🧾 Contas":
    if st.button("← Voltar ao Início",key="voltar_inicio_contas"):
        st.session_state["_page_destino"]="🏠 Início"
        st.rerun()
    st.title("🧾 Minhas contas")
    st.caption("Cadastre contas com vencimento, como celular, energia, aluguel ou escola.")
    with st.form("conta",clear_on_submit=True):
        n=st.text_input("Conta");a,b=st.columns(2);cat=a.selectbox("Categoria",["Moradia","Saúde","Transporte","Assinaturas","Outros"]);v=b.number_input("Valor",min_value=0.0);a,b=st.columns(2);ven=a.date_input("Vencimento");status=b.selectbox("Status",["Pendente","Pago","Atrasado"])
        if st.form_submit_button("Adicionar"):sb.table("contas").insert({"user_id":st.session_state.uid,"nome":n,"categoria":cat,"valor":v,"vencimento":ven.isoformat(),"status":status}).execute();st.success("Adicionada.")
    st.dataframe(pd.DataFrame(myrows("contas","vencimento")),use_container_width=True,hide_index=True)

elif page=="💳 Cartões":
    st.title("💳 Meus cartões")
    st.caption("Cadastre seus cartões e acompanhe a fatura atual.")
    if st.button("← Voltar ao Início",key="voltar_inicio_cards"):
        st.session_state["_page_destino"]="🏠 Início"; st.rerun()
    with st.form("card",clear_on_submit=True):
        n=st.text_input("Cartão");a,b=st.columns(2);lim=a.number_input("Limite",min_value=0.0);fat=b.number_input("Fatura atual",min_value=0.0);a,b=st.columns(2);fec=a.number_input("Fechamento",1,28,10);ven=b.number_input("Vencimento",1,28,20)
        if st.form_submit_button("Adicionar"):sb.table("cartoes").insert({"user_id":st.session_state.uid,"nome":n,"limite":lim,"fatura":fat,"fechamento":fec,"vencimento":ven}).execute();st.success("Adicionado.")
    st.dataframe(pd.DataFrame(myrows("cartoes")),use_container_width=True,hide_index=True)

elif page=="📉 Dívidas":
    st.title("📉 Dívidas")
    with st.form("div",clear_on_submit=True):
        n=st.text_input("Dívida");a,b,c=st.columns(3);saldo=a.number_input("Saldo",min_value=0.0);par=b.number_input("Parcela",min_value=0.0);rest=c.number_input("Parcelas restantes",min_value=0,step=1)
        if st.form_submit_button("Adicionar"):sb.table("dividas").insert({"user_id":st.session_state.uid,"nome":n,"saldo":saldo,"parcela":par,"restantes":rest}).execute();st.success("Adicionada.")
    st.dataframe(pd.DataFrame(myrows("dividas")),use_container_width=True,hide_index=True)

elif page=="🎯 Metas":
    st.title("🎯 Metas")
    st.caption("Acompanhe seus objetivos e atualize o valor guardado sem complicação.")
    with st.expander("➕ Criar nova meta",expanded=False):
        with st.form("meta",clear_on_submit=True):
            n=st.text_input("Objetivo",placeholder="Ex.: Viagem, reserva, carro")
            a,b=st.columns(2);d=a.number_input("Valor desejado",min_value=0.0);g=b.number_input("Já guardado",min_value=0.0)
            if st.form_submit_button("➕ Adicionar meta",use_container_width=True):
                if not n.strip() or d<=0: st.warning("Informe o objetivo e um valor desejado maior que zero.")
                else:
                    sb.table("metas").insert({"user_id":st.session_state.uid,"nome":n.strip(),"desejado":d,"guardado":g}).execute();st.cache_data.clear();st.rerun()
    _metas=myrows("metas")
    if not _metas: st.info("Você ainda não criou nenhuma meta. Use **Criar nova meta** para começar.")
    for _m in _metas:
        _d=float(_m.get("desejado") or 0); _g=float(_m.get("guardado") or 0); _p=min(_g/_d,1.0) if _d>0 else 0
        with st.container(border=True):
            a,b=st.columns([4,1]);a.markdown(f"### 🎯 {_m.get('nome','Meta')}");b.markdown(f"**{int(_p*100)}%**")
            st.progress(_p,text=f"{money(_g)} de {money(_d)}")
            c1,c2,c3=st.columns(3);c1.metric("Guardado",money(_g));c2.metric("Falta",money(max(_d-_g,0)));c3.metric("Objetivo",money(_d))
            if st.button("⚙️ Atualizar meta",use_container_width=True,key=f"manage_goal_{_m['id']}"): manage_goal_dialog(_m)

elif page=="📊 Relatórios":
    st.title("📊 Relatórios");d=pd.DataFrame(myrows("mov","data"))
    _,rec_total,rec_pendente=recurring_summary()
    st.caption(f"🔁 Recorrentes: {money(rec_total)}/mês • pendentes neste mês: {money(rec_pendente)}")
    if len(d):
        s=d[d.tipo=="Saída"].groupby("categoria")["valor"].sum();st.bar_chart(s);st.dataframe(d,use_container_width=True,hide_index=True)
    else:st.info("Ainda não há lançamentos.")

else:
    st.title("⚙️ Configurações")
    st.info("As rendas agora são gerenciadas em ⚙️ Minha renda, com múltiplos titulares e fontes.")
    with st.form("cfg"):
        a,b=st.columns(2);r1=a.number_input("1º recebimento",min_value=0.0,value=float(cfg["rec1"]));r2=b.number_input("2º recebimento",min_value=0.0,value=float(cfg["rec2"]));a,b=st.columns(2);d2=a.number_input("Dia do 2º recebimento",1,28,int(cfg["dia2"]));pct=b.slider("Porcentagem para guardar",0,50,int(cfg["pct"]));res=st.number_input("Reserva mínima",min_value=0.0,value=float(cfg["reserva"]))
        st.caption(f"Meta mensal para guardar: {money((r1+r2)*pct/100)}.")
        if st.form_submit_button("💾 Salvar",use_container_width=True):
            sb.table("config").update({"rec1":r1,"rec2":r2,"dia2":d2,"pct":pct,"reserva":res}).eq("user_id",st.session_state.uid).execute();st.success("Configurações salvas.")
