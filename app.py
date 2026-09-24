import streamlit as st
import pandas as pd
from datetime import date, timedelta
import calendar
import json
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
    """Retorna parcelamentos ativos e a parcela que ainda precisa ser paga no mês atual."""
    try:
        itens=myrows("parcelamentos","dia_vencimento")
    except Exception:
        return [],0.0
    mes=date.today().strftime("%Y-%m")
    ativos=[x for x in itens if bool(x.get("ativo",True)) and int(x.get("parcelas_pagas") or 0) < int(x.get("total_parcelas") or 0)]
    pendente=sum(float(x.get("valor_parcela") or 0) for x in ativos if x.get("ultimo_pago_mes") != mes)
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



st.markdown("""
<style>
/* Modal do tutorial — compatível com diferentes estruturas do Streamlit */
[data-testid="stDialog"],
[data-testid="stDialog"] > div,
[data-testid="stDialog"] section,
div[role="dialog"],
div[role="dialog"] > div {
    background: #FFFFFF !important;
    background-color: #FFFFFF !important;
    color: #172033 !important;
}

[data-testid="stDialog"] *,
div[role="dialog"] * {
    color: #172033 !important;
}

[data-testid="stDialog"] [data-testid="stAlert"],
div[role="dialog"] [data-testid="stAlert"] {
    background: #EAF4FF !important;
    background-color: #EAF4FF !important;
    border-color: #B8D9FF !important;
}

[data-testid="stDialog"] button[kind="primary"],
[data-testid="stDialog"] button[kind="primary"] *,
div[role="dialog"] button[kind="primary"],
div[role="dialog"] button[kind="primary"] * {
    color: #FFFFFF !important;
}

/* Mantém a área atrás do tutorial apenas suavemente escurecida */
[data-testid="stModal"] {
    background-color: rgba(16, 25, 46, 0.18) !important;
}
</style>
""", unsafe_allow_html=True)

@st.dialog("👋 Bem-vindo ao Meu Financeiro", width="large")
def tutorial_dialog():
    st.caption("Guia rápido para você saber onde começar e para que serve cada área.")

    st.markdown("### 1️⃣ Configure sua renda")
    st.write("Abra **⚙️ Configurações** e informe seus recebimentos, o percentual que deseja guardar e a sua reserva mínima.")

    st.markdown("### 2️⃣ Registre entradas e gastos")
    st.write("Em **➕ Registrar**, digite a descrição e o sistema sugere uma categoria. Se você corrigir a categoria e salvar, o Meu Financeiro reaproveita sua escolha quando a mesma descrição aparecer novamente. Ele também identifica possíveis gastos recorrentes e permite transformá-los em recorrentes sem cadastrar tudo de novo.")

    st.markdown("### 3️⃣ Converse com o Assistente IA")
    st.write("Em **🤖 Assistente IA**, faça perguntas sobre os seus próprios números, como quanto pode gastar, onde gastou mais e como está o mês. Os três botões rápidos sempre substituem a análise anterior, deixando a tela limpa; perguntas digitadas no chat continuam formando uma conversa. Os atalhos principais são calculados pelo próprio sistema e funcionam mesmo quando o serviço externo de IA está ocupado. Para perguntas livres, a IA usa somente um resumo dos valores financeiros necessários e, se o serviço estiver indisponível, o sistema responde com uma análise local.")

    st.markdown("### 4️⃣ Acompanhe sua Saúde Financeira")
    st.write("Em **🚦 Saúde Financeira**, veja quanto da sua renda está comprometida e acompanhe o indicador **Tranquilo, Atenção ou Orçamento apertado**.")

    st.markdown("### 5️⃣ Veja sua Previsão Financeira")
    st.write("Em **🔮 Previsão**, acompanhe uma estimativa do fim do mês com base no seu ritmo de gastos, contas pendentes e meta de economia. Você também pode comparar três cenários.")

    st.markdown("### 6️⃣ Cadastre gastos recorrentes")
    st.write("Em **🔁 Recorrentes**, cadastre compromissos que se repetem todos os meses, como internet, streaming, academia e mensalidades. Marque como pago no mês para evitar que o valor continue aparecendo como compromisso pendente.")

    st.markdown("### 7️⃣ Controle compras parceladas")
    st.write("Em **💳 Parcelamentos**, informe a compra, valor da parcela, total de parcelas e quantas já foram pagas. O sistema mostra o progresso, saldo restante, próxima parcela e inclui automaticamente a parcela pendente nos cálculos e vencimentos. Ao marcar a parcela do mês como paga, ela vira gasto realizado sem ser contada duas vezes.")

    st.markdown("### 7️⃣ Organize suas contas")
    st.write("Em **🧾 Contas**, cadastre contas a pagar, vencimentos e marque cada uma como Pendente, Pago ou Atrasado.")

    st.markdown("### 8️⃣ Acompanhe seus cartões")
    st.write("Em **💳 Cartões**, informe limite, fatura atual, dia de fechamento e vencimento.")

    st.markdown("### 9️⃣ Controle dívidas e objetivos")
    st.write("Use **📉 Dívidas** para acompanhar saldo e parcelas. Em **🎯 Metas**, registre quanto deseja juntar e quanto já guardou.")

    st.markdown("### 🔟 Entenda a tela Início")
    st.write("Em **🏠 Início**, acompanhe saldo disponível, próximo pagamento, quanto guardar e o limite seguro por dia. A área **🔔 Próximos vencimentos** reúne contas e recorrentes ainda pendentes, mostrando o que vence hoje, nos próximos dias ou está atrasado. O saldo e o limite usam a mesma base do Assistente IA, Saúde Financeira e Previsão, considerando gastos, contas, recorrentes, faturas e sua meta de guardar.")

    st.markdown("### 1️⃣1️⃣ Consulte seus relatórios")
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
    aprendida=categoria_aprendida(descricao)
    if aprendida:
        return aprendida
    texto=(descricao or "").lower().strip()
    regras={
        "Transporte":["uber","99","taxi","táxi","posto","gasolina","etanol","combustivel","combustível","pedagio","pedágio","estacionamento"],
        "Alimentação":["ifood","restaurante","lanche","pizza","hamburguer","hambúrguer","padaria","cafe","café","almoço","almoco","jantar"],
        "Assinaturas":["netflix","spotify","disney","prime video","amazon prime","youtube premium","hbo","max","deezer","icloud","google one"],
        "Saúde":["farmacia","farmácia","drogaria","medico","médico","consulta","dentista","hospital","exame"],
        "Moradia":["aluguel","condominio","condomínio","energia","luz","agua","água","internet","iptu"],
        "Compras":["shopee","mercado livre","amazon","magalu","magazine luiza","roupa","calçado","calcado"],
        "Lazer":["cinema","show","bar","viagem","hotel","ingresso","jogo"],
    }
    for categoria, termos in regras.items():
        if any(t in texto for t in termos):
            return categoria
    return "Outros"

page=st.sidebar.radio("Menu",["🏠 Início","🤖 Assistente IA","🚦 Saúde Financeira","🔮 Previsão","➕ Registrar","🔁 Recorrentes","💳 Parcelamentos","🧾 Contas","💳 Cartões","📉 Dívidas","🎯 Metas","📊 Relatórios","⚙️ Configurações"])

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
    sal=float(cfg["rec1"]+cfg["rec2"]);guardar=sal*float(cfg["pct"])/100;viver=sal-guardar
    nd,nv=nextpay(cfg["dia2"],cfg["rec1"],cfg["rec2"]);dias=max((nd-t).days,0)
    contas=myrows("contas");pend=sum(float(x["valor"]) for x in contas if x["status"]!="Pago")
    cards=myrows("cartoes");fatura=sum(float(x["fatura"]) for x in cards)
    _,rec_total,rec_pendente=recurring_summary()
    _,parc_pendente=installment_summary()
    # Saldo realmente livre para uso: renda disponível menos gastos já feitos e compromissos ainda pendentes.
    saldo=max(viver+extras-gastos-pend-rec_pendente-parc_pendente-fatura,0)
    # Contas e recorrentes já foram descontados de saldo; não descontar novamente no limite diário.
    livre=max(saldo-float(cfg["reserva"]),0);diario=livre/max(dias,1)
    a,b,c,d=st.columns(4);a.metric("💵 Saldo para usar",money(saldo));b.metric("📅 Próximo pagamento",money(nv),f"{dias} dia(s)");c.metric("🐷 Guardar no mês",money(guardar),f"{cfg['pct']:.0f}% da renda");d.metric("📈 Limite seguro/dia",money(diario))
    st.subheader("🤖 Assistente financeiro")
    if sal<=0:st.info("Comece em Configurações e informe seus dois recebimentos.")
    elif saldo<=0:st.error(f"Seu dinheiro disponível acabou. Faltam {dias} dia(s) para receber.")
    elif saldo<=float(cfg["reserva"]):st.error("Você chegou à reserva mínima. Evite gastos não essenciais.")
    elif viver>0 and gastos>=viver*.9:st.error("Não gaste muito: você já utilizou quase todo o dinheiro planejado.")
    elif viver>0 and gastos>=viver*.7:st.warning(f"Cuidado. Até receber, tente ficar abaixo de {money(diario)} por dia.")
    else:st.success(f"Tudo dentro do planejado. Preserve {money(guardar)} e tente gastar até {money(diario)} por dia.")
    st.subheader("Visão geral");x,y,z,w=st.columns(4);x.metric("Receita mensal",money(sal+extras));y.metric("Gastos",money(gastos));z.metric("Contas + compromissos",money(pend+rec_pendente+parc_pendente));w.metric("Faturas",money(fatura))

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

elif page=="🤖 Assistente IA":
    st.title("🤖 Assistente Financeiro IA")
    st.caption("Converse sobre seus próprios dados financeiros. O assistente analisa informações da sua conta, mas não movimenta dinheiro e não acessa senhas ou dados completos de cartão.")

    # Monta um resumo financeiro limitado ao usuário autenticado.
    t=date.today();ini=t.replace(day=1);fim=date(t.year,t.month,calendar.monthrange(t.year,t.month)[1])
    mov=pd.DataFrame(myrows("mov","data"))
    if len(mov):
        mov["data"]=pd.to_datetime(mov["data"]).dt.date
        mm=mov[(mov.data>=ini)&(mov.data<=fim)].copy()
    else:mm=pd.DataFrame()

    gastos=float(mm.loc[mm.tipo=="Saída","valor"].sum()) if len(mm) else 0
    extras=float(mm.loc[mm.tipo=="Entrada","valor"].sum()) if len(mm) else 0
    renda_base=float(cfg["rec1"]+cfg["rec2"])
    guardar=renda_base*float(cfg["pct"])/100
    contas=myrows("contas")
    cards=myrows("cartoes")
    dividas=myrows("dividas")
    metas=myrows("metas")
    pend=sum(float(x["valor"]) for x in contas if x["status"]!="Pago")
    fatura=sum(float(x["fatura"]) for x in cards)
    recorrentes,rec_total,rec_pendente=recurring_summary()
    # O saldo informado à IA deve refletir também contas e recorrentes ainda pendentes.
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
    c.metric("🧾 Contas + recorrentes",money(pend+rec_pendente))
    d.metric("💳 Faturas",money(fatura))
    if rec_total>0: st.caption(f"🔁 Recorrentes cadastrados: {money(rec_total)}/mês • ainda pendentes neste mês: {money(rec_pendente)}")

    st.info("🔒 Para responder, a IA recebe apenas um resumo dos seus valores financeiros. Descrições individuais das suas movimentações não são enviadas. Nunca informe senha bancária, CVV ou número completo de cartão no chat.")

    def resposta_financeira_local(pergunta_local):
        """Fallback local: responde perguntas financeiras comuns sem depender do Gemini."""
        q=(pergunta_local or "").lower()
        # Mesma regra da Home: limite seguro considera a reserva mínima e os dias até o próximo recebimento.
        nd_ia,_=nextpay(cfg["dia2"],cfg["rec1"],cfg["rec2"])
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

    pergunta=st.chat_input("Pergunte algo sobre suas finanças...")
    if pergunta:
        st.session_state.ai_quick=False
        st.session_state.ai_question=pergunta

    if "ai_history" not in st.session_state:
        st.session_state.ai_history=[]

    for item in st.session_state.ai_history[-8:]:
        with st.chat_message(item["role"]):
            st.markdown(item["content"])

    pergunta_atual=st.session_state.pop("ai_question",None)
    if pergunta_atual:
        with st.chat_message("user"):
            st.markdown(pergunta_atual)

        # Os 3 atalhos principais são calculados localmente. Assim funcionam sempre,
        # mesmo se o serviço externo de IA estiver congestionado.
        pergunta_rapida=bool(st.session_state.pop("ai_quick",False))
        api_key=st.secrets.get("GEMINI_API_KEY",None)

        if pergunta_rapida:
            resposta=resposta_financeira_local(pergunta_atual)
        elif not api_key:
            resposta=resposta_financeira_local(pergunta_atual)
        else:
            try:
                from google import genai
                from google.genai import types
                client=genai.Client(api_key=api_key,http_options=types.HttpOptions(timeout=15000))
                instrucoes="""Você é o Assistente Financeiro do aplicativo Meu Financeiro.
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
                # Modelos estáveis em sequência. Se todos estiverem indisponíveis,
                # o assistente responde localmente em vez de falhar.
                modelos=["gemini-3.8-flash","gemini-3.6-flash","gemini-3.5-flash-lite"]
                resposta=None
                for modelo in modelos:
                    try:
                        resp=client.models.generate_content(
                            model=modelo,
                            contents=contexto+"\n\nPERGUNTA DO USUÁRIO:\n"+pergunta_atual,
                            config=types.GenerateContentConfig(
                                system_instruction=instrucoes,
                                max_output_tokens=700
                            )
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

        # Remove cercas de código acidentais que podem prejudicar a leitura de valores.
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
    renda_base=float(cfg["rec1"]+cfg["rec2"])
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
    renda=float(cfg["rec1"]+cfg["rec2"])
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
    st.title("🔁 Gastos Recorrentes")
    st.caption("Cadastre compromissos mensais. Eles entram automaticamente no planejamento enquanto não forem marcados como pagos no mês.")
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
    st.title("💳 Compras Parceladas")
    st.caption("Acompanhe compras com número definido de parcelas. A parcela do mês entra automaticamente no planejamento enquanto estiver pendente.")
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
                sb.table("parcelamentos").update({"ultimo_pago_mes":mes,"parcelas_pagas":min(pagas+1,total)}).eq("id",x["id"]).eq("user_id",st.session_state.uid).execute(); sync_installment_payments(); st.rerun()
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
    _,rec_total,rec_pendente=recurring_summary()
    st.caption(f"🔁 Recorrentes: {money(rec_total)}/mês • pendentes neste mês: {money(rec_pendente)}")
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
