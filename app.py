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
    st.title("💰 Meu Financeiro Online")
    st.caption("Entre na sua conta para acessar seus dados financeiros.")
    tab1, tab2 = st.tabs(["Entrar", "Criar conta"])

    with tab1:
        with st.form("login"):
            email = st.text_input("E-mail")
            senha = st.text_input("Senha", type="password")
            if st.form_submit_button("Entrar", use_container_width=True):
                try:
                    r = sb.auth.sign_in_with_password({"email": email, "password": senha})
                    if save_auth_session(r):
                        st.rerun()
                    else:
                        st.error("Não foi possível iniciar a sessão.")
                except Exception as e:
                    st.error(f"Não foi possível entrar: {e}")

        with st.expander("🔑 Esqueci minha senha"):
            recovery_email = st.text_input("E-mail da conta", key="recovery_email")
            if st.button("Enviar e-mail de recuperação", use_container_width=True):
                if not recovery_email.strip():
                    st.warning("Informe seu e-mail.")
                else:
                    try:
                        sb.auth.reset_password_for_email(
                            recovery_email.strip(),
                            {"redirect_to": APP_URL}
                        )
                        st.success("E-mail de recuperação enviado. Abra a mensagem e clique no link para criar uma nova senha.")
                    except Exception as e:
                        st.error(f"Não foi possível enviar o e-mail de recuperação: {e}")

    with tab2:
        with st.form("signup"):
            email = st.text_input("Seu e-mail", key="se")
            senha = st.text_input("Crie uma senha", type="password", key="ss")
            senha2 = st.text_input("Repita a senha", type="password")
            if st.form_submit_button("Criar minha conta", use_container_width=True):
                if len(senha) < 6:
                    st.error("Use uma senha com pelo menos 6 caracteres.")
                elif senha != senha2:
                    st.error("As senhas não são iguais.")
                else:
                    try:
                        r = sb.auth.sign_up({
                            "email": email,
                            "password": senha,
                            "options": {"email_redirect_to": APP_URL}
                        })
                        if r.session:
                            save_auth_session(r)
                            st.success("Conta criada com sucesso.")
                            st.rerun()
                        else:
                            st.success("Conta criada. Verifique seu e-mail e confirme a conta antes de entrar.")
                    except Exception as e:
                        st.error(f"Erro ao criar conta: {e}")


@st.dialog("👋 Bem-vindo ao Meu Financeiro", width="large")
def tutorial_dialog():
    st.caption("Guia rápido para você saber onde começar e para que serve cada área.")

    st.markdown("### 1️⃣ Configure sua renda")
    st.write("Abra **⚙️ Configurações** e informe o 1º e o 2º recebimento, o percentual que deseja guardar e a sua reserva mínima.")

    st.markdown("### 2️⃣ Registre entradas e gastos")
    st.write("Em **➕ Registrar**, anote cada entrada ou saída, escolha a categoria, a forma de pagamento e o valor.")

    st.markdown("### 3️⃣ Organize suas contas")
    st.write("Em **🧾 Contas**, cadastre contas a pagar, vencimentos e marque cada uma como Pendente, Pago ou Atrasado.")

    st.markdown("### 4️⃣ Acompanhe seus cartões")
    st.write("Em **💳 Cartões**, informe limite, fatura atual, dia de fechamento e vencimento.")

    st.markdown("### 5️⃣ Controle dívidas e objetivos")
    st.write("Use **📉 Dívidas** para acompanhar saldo e parcelas. Em **🎯 Metas**, registre quanto deseja juntar e quanto já guardou.")

    st.markdown("### 6️⃣ Entenda a tela Início")
    st.write("Em **🏠 Início**, acompanhe saldo disponível, próximo pagamento, quanto guardar e o limite seguro de gasto por dia. O Assistente Financeiro avisa quando os gastos estiverem se aproximando do planejado.")

    st.markdown("### 7️⃣ Consulte seus relatórios")
    st.write("Em **📊 Relatórios**, veja seus lançamentos e a distribuição dos gastos por categoria.")

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
st.session_state.tutorial_oculto = bool(cfg.get("tutorial_oculto", False))

st.sidebar.title("💰 Meu Financeiro")
st.sidebar.caption(st.session_state.get("email",""))
page=st.sidebar.radio("Menu",["🏠 Início","➕ Registrar","🧾 Contas","💳 Cartões","📉 Dívidas","🎯 Metas","📊 Relatórios","⚙️ Configurações"])

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
