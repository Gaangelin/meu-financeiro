import streamlit as st
import pandas as pd
from datetime import date, timedelta
import calendar
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
div.stButton>button,div[data-testid="stFormSubmitButton"] button{border-radius:12px;font-weight:700}
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

def ensure_config():
    r=sb.table("config").select("*").eq("user_id",st.session_state.uid).execute().data
    if not r:
        sb.table("config").insert({"user_id":st.session_state.uid,"rec1":0,"rec2":0,"dia2":20,"pct":20,"reserva":0}).execute()
        r=sb.table("config").select("*").eq("user_id",st.session_state.uid).execute().data
    return r[0]

def login():
    st.title("💰 Meu Financeiro Online")
    st.caption("Entre na sua conta para acessar seus dados financeiros.")
    tab1,tab2=st.tabs(["Entrar","Criar conta"])
    with tab1:
        with st.form("login"):
            email=st.text_input("E-mail")
            senha=st.text_input("Senha",type="password")
            if st.form_submit_button("Entrar",use_container_width=True):
                try:
                    r=sb.auth.sign_in_with_password({"email":email,"password":senha})
                    st.session_state.uid=r.user.id;st.session_state.email=r.user.email;st.rerun()
                except Exception:st.error("E-mail ou senha incorretos, ou conta ainda não confirmada.")
    with tab2:
        with st.form("signup"):
            email=st.text_input("Seu e-mail",key="se")
            senha=st.text_input("Crie uma senha",type="password",key="ss")
            senha2=st.text_input("Repita a senha",type="password")
            if st.form_submit_button("Criar minha conta",use_container_width=True):
            if st.form_submit_button("Criar minha conta", use_container_width=True):
                if len(senha) < 6:
                    st.error("Use uma senha com pelo menos 6 caracteres.")
                elif senha != senha2:
                    st.error("As senhas não são iguais.")
                else:
                    try:
                        sb.auth.sign_up({"email": email, "password": senha})
                        st.success("Conta criada. Verifique seu e-mail para confirmar a conta antes de entrar.")
                    except Exception as e:
                        st.error(f"Erro ao criar conta: {e}")
if "uid" not in st.session_state:
    login();st.stop()

cfg=ensure_config()
st.sidebar.title("💰 Meu Financeiro")
st.sidebar.caption(st.session_state.get("email",""))
page=st.sidebar.radio("Menu",["🏠 Início","➕ Registrar","🧾 Contas","💳 Cartões","📉 Dívidas","🎯 Metas","📊 Relatórios","⚙️ Configurações"])
if st.sidebar.button("🚪 Sair",use_container_width=True):
    try:sb.auth.sign_out()
    except:pass
    st.session_state.clear();st.rerun()
st.sidebar.divider();st.sidebar.caption("🔒 Cada conta acessa somente os próprios dados.")

if page=="🏠 Início":
    st.title("Meu Financeiro")
    st.caption("Seu dinheiro, seus objetivos e o próximo pagamento em um só lugar.")
    t=date.today();ini=t.replace(day=1);fim=date(t.year,t.month,calendar.monthrange(t.year,t.month)[1])
    mov=pd.DataFrame(myrows("mov","data"))
    if len(mov):
        mov["data"]=pd.to_datetime(mov["data"]).dt.date
        mm=mov[(mov.data>=ini)&(mov.data<=fim)]
    else:mm=pd.DataFrame()
    gastos=float(mm.loc[mm.tipo=="Saída","valor"].sum()) if len(mm) else 0
    extras=float(mm.loc[mm.tipo=="Entrada","valor"].sum()) if len(mm) else 0
    sal=float(cfg["rec1"]+cfg["rec2"]);guardar=sal*float(cfg["pct"])/100;viver=sal-guardar;saldo=max(viver+extras-gastos,0)
    nd,nv=nextpay(cfg["dia2"],cfg["rec1"],cfg["rec2"]);dias=max((nd-t).days,0)
    contas=myrows("contas");pend=sum(float(x["valor"]) for x in contas if x["status"]!="Pago")
    cards=myrows("cartoes");fatura=sum(float(x["fatura"]) for x in cards)
    livre=max(saldo-float(cfg["reserva"])-pend,0);diario=livre/max(dias,1)
    a,b,c,d=st.columns(4);a.metric("💵 Saldo para usar",money(saldo));b.metric("📅 Próximo pagamento",money(nv),f"{dias} dia(s)");c.metric("🐷 Guardar no mês",money(guardar),f"{cfg['pct']:.0f}% da renda");d.metric("📈 Limite seguro/dia",money(diario))
    st.subheader("🤖 Assistente financeiro")
    if sal<=0:st.info("Comece em Configurações e informe seus dois recebimentos.")
    elif saldo<=0:st.error(f"Seu dinheiro disponível acabou. Faltam {dias} dia(s) para receber.")
    elif saldo<=float(cfg["reserva"]):st.error("Você chegou à reserva mínima. Evite gastos não essenciais.")
    elif viver>0 and gastos>=viver*.9:st.error("Não gaste muito: você já utilizou quase todo o dinheiro planejado.")
    elif viver>0 and gastos>=viver*.7:st.warning(f"Cuidado. Até receber, tente ficar abaixo de {money(diario)} por dia.")
    else:st.success(f"Tudo dentro do planejado. Preserve {money(guardar)} e tente gastar até {money(diario)} por dia.")
    st.subheader("Visão geral");x,y,z,w=st.columns(4);x.metric("Receita mensal",money(sal+extras));y.metric("Gastos",money(gastos));z.metric("Contas pendentes",money(pend));w.metric("Faturas",money(fatura))
    if len(mm):
        s=mm[mm.tipo=="Saída"].groupby("categoria")["valor"].sum()
        if len(s):st.subheader("Gastos por categoria");st.bar_chart(s)

elif page=="➕ Registrar":
    st.title("➕ Registrar")
    with st.form("mov",clear_on_submit=True):
        dt=st.date_input("Data",date.today());desc=st.text_input("Descrição");a,b=st.columns(2);cat=a.selectbox("Categoria",["Moradia","Alimentação","Transporte","Saúde","Lazer","Assinaturas","Compras","Outros"]);tipo=b.selectbox("Tipo",["Saída","Entrada"]);a,b=st.columns(2);forma=a.selectbox("Forma",["Pix","Débito","Crédito","Dinheiro","Outro"]);valor=b.number_input("Valor",min_value=0.0);obs=st.text_input("Observação")
        if st.form_submit_button("💾 Salvar",use_container_width=True):
            sb.table("mov").insert({"user_id":st.session_state.uid,"data":dt.isoformat(),"descricao":desc,"categoria":cat,"tipo":tipo,"forma":forma,"valor":valor,"obs":obs}).execute();st.success("Salvo.")
    st.dataframe(pd.DataFrame(myrows("mov","data")),use_container_width=True,hide_index=True)

elif page=="🧾 Contas":
    st.title("🧾 Contas")
    with st.form("conta",clear_on_submit=True):
        n=st.text_input("Conta");a,b=st.columns(2);cat=a.selectbox("Categoria",["Moradia","Saúde","Transporte","Assinaturas","Outros"]);v=b.number_input("Valor",min_value=0.0);a,b=st.columns(2);ven=a.date_input("Vencimento");status=b.selectbox("Status",["Pendente","Pago","Atrasado"])
        if st.form_submit_button("Adicionar"):sb.table("contas").insert({"user_id":st.session_state.uid,"nome":n,"categoria":cat,"valor":v,"vencimento":ven.isoformat(),"status":status}).execute();st.success("Adicionada.")
    st.dataframe(pd.DataFrame(myrows("contas","vencimento")),use_container_width=True,hide_index=True)

elif page=="💳 Cartões":
    st.title("💳 Cartões")
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
    with st.form("meta",clear_on_submit=True):
        n=st.text_input("Objetivo");a,b=st.columns(2);d=a.number_input("Valor desejado",min_value=0.0);g=b.number_input("Já guardado",min_value=0.0)
        if st.form_submit_button("Adicionar"):sb.table("metas").insert({"user_id":st.session_state.uid,"nome":n,"desejado":d,"guardado":g}).execute();st.success("Adicionada.")
    st.dataframe(pd.DataFrame(myrows("metas")),use_container_width=True,hide_index=True)

elif page=="📊 Relatórios":
    st.title("📊 Relatórios");d=pd.DataFrame(myrows("mov","data"))
    if len(d):
        s=d[d.tipo=="Saída"].groupby("categoria")["valor"].sum();st.bar_chart(s);st.dataframe(d,use_container_width=True,hide_index=True)
    else:st.info("Ainda não há lançamentos.")

else:
    st.title("⚙️ Configurações")
    st.info("1º recebimento: 5º dia útil. 2º recebimento: dia 20 por padrão.")
    with st.form("cfg"):
        a,b=st.columns(2);r1=a.number_input("1º recebimento",min_value=0.0,value=float(cfg["rec1"]));r2=b.number_input("2º recebimento",min_value=0.0,value=float(cfg["rec2"]));a,b=st.columns(2);d2=a.number_input("Dia do 2º recebimento",1,28,int(cfg["dia2"]));pct=b.slider("Porcentagem para guardar",0,50,int(cfg["pct"]));res=st.number_input("Reserva mínima",min_value=0.0,value=float(cfg["reserva"]))
        st.caption(f"Meta mensal para guardar: {money((r1+r2)*pct/100)}.")
        if st.form_submit_button("💾 Salvar",use_container_width=True):
            sb.table("config").update({"rec1":r1,"rec2":r2,"dia2":d2,"pct":pct,"reserva":res}).eq("user_id",st.session_state.uid).execute();st.success("Configurações salvas.")
