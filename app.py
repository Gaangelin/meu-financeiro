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


@st.dialog("👋 Bem-vindo ao Meu Financeiro", width="large")
def tutorial_dialog():
    st.caption("Guia rápido para você saber onde começar e para que serve cada área.")

    st.markdown("### 1️⃣ Configure sua renda")
    st.write("Na tela **🏠 Início**, clique em **⚙️ Minha renda** e informe quanto você recebe, quanto quer guardar e quanto quer deixar como reserva. Esses valores alimentam automaticamente os cálculos do Meu Financeiro.")

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

    st.markdown("### 8️⃣ Organize os bancos da família")
    st.write("Em **🏦 Bancos**, você pode cadastrar quantas contas quiser e indicar o **titular** de cada uma, por exemplo você e sua esposa. A Central mostra o saldo por titular e o saldo familiar, permite filtrar, editar e excluir contas. Por enquanto esses saldos ficam separados do cálculo principal da tela Início. A futura conexão Open Finance será feita por autorização segura e nunca pedirá sua senha bancária dentro do Meu Financeiro.")

    st.markdown("### 9️⃣ Organize suas contas")
    st.write("Em **🧾 Contas**, cadastre contas a pagar, vencimentos e marque cada uma como Pendente, Pago ou Atrasado.")

    st.markdown("### 🔟 Acompanhe seus cartões")
    st.write("Em **💳 Cartões**, informe limite, fatura atual, dia de fechamento e vencimento.")

    st.markdown("### 1️⃣1️⃣ Controle dívidas e objetivos")
    st.write("Use **📉 Dívidas** para acompanhar saldo e parcelas. Em **🎯 Metas**, registre quanto deseja juntar e quanto já guardou.")

    st.markdown("### 🧭 Menu simplificado")
    st.write("O menu principal agora mostra apenas as áreas essenciais. As funções de Registrar, Contas, Recorrentes, Parcelamentos, Cartões, Previsão e Relatórios continuam preservadas no sistema enquanto são reorganizadas para um uso mais simples pela tela **🏠 Início**.")

    st.markdown("### ✨ Modo Simples")
    st.write("Na tela **🏠 Início**, os atalhos **Recebi dinheiro**, **Gastei dinheiro** e **Tenho uma conta** ajudam usuários iniciantes a encontrar rapidamente a função certa. Em **Posso comprar?**, informe o valor e escolha à vista ou parcelado para ver o impacto antes da compra. Em **📂 Meus compromissos**, contas, gastos mensais e parcelas pendentes aparecem diretamente no Início com o botão **Paguei**. Ao pagar uma parcela, o sistema avança automaticamente; na última, o parcelamento é concluído.")

    st.markdown("### 1️⃣2️⃣ Entenda a tela Início")
    st.write("Em **🏠 Início**, acompanhe saldo disponível, próximo pagamento, quanto guardar e o limite seguro por dia. A área **🔔 Próximos vencimentos** reúne contas, recorrentes e parcelas ainda pendentes, mostrando o que vence hoje, nos próximos dias ou está atrasado.")

    st.markdown("### 1️⃣3️⃣ Consulte seus relatórios")
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

menu_visivel=["🏠 Início","🏦 Bancos","🤖 Assistente IA","🚦 Saúde Financeira","📉 Dívidas","🎯 Metas"]

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

    st.subheader("⚡ Modo Simples")
    st.caption("Para quem quer cuidar do dinheiro sem precisar entender termos financeiros.")
    sm1,sm2,sm3,sm4,sm5=st.columns(5)
    acao_simples=None
    if sm1.button("💰 Recebi dinheiro",use_container_width=True,key="simple_income"):
        acao_simples="recebi"
    if sm2.button("🛒 Gastei dinheiro",use_container_width=True,key="simple_expense"):
        acao_simples="gastei"
    if sm3.button("🧾 Tenho uma conta",use_container_width=True,key="simple_bill"):
        acao_simples="conta"
    if sm4.button("🛍️ Posso comprar?",use_container_width=True,key="simple_buy"):
        st.session_state["_page_destino"]="🛍️ Posso comprar?"
        st.rerun()
    if sm5.button("⚙️ Minha renda",use_container_width=True,key="simple_income_cfg"):
        st.session_state["_page_destino"]="⚙️ Minha renda"
        st.rerun()

    if acao_simples=="recebi":
        st.session_state["_page_destino"]="➕ Registrar"
        st.session_state["_registro_tipo_inicial"]="Entrada"
        st.rerun()
    elif acao_simples=="gastei":
        st.session_state["_page_destino"]="➕ Registrar"
        st.session_state["_registro_tipo_inicial"]="Saída"
        st.rerun()
    elif acao_simples=="conta":
        st.session_state["_page_destino"]="🧾 Contas"
        st.rerun()

    st.divider()

    st.markdown("### 📂 Meus compromissos")
    st.caption("Veja o que está pendente e marque como pago sem precisar procurar em outras telas.")

    mes_comp=date.today().strftime("%Y-%m")
    rec_home,_,_=recurring_summary()
    parc_home,_=installment_summary()
    contas_home=[x for x in contas if x.get("status")!="Pago"]
    rec_home_pend=[x for x in rec_home if x.get("ultimo_pago_mes")!=mes_comp]
    parc_home_pend=[x for x in parc_home if x.get("ultimo_pago_mes")!=mes_comp]

    compromissos_home=[]
    for x in contas_home:
        compromissos_home.append(("conta",x))
    for x in rec_home_pend:
        compromissos_home.append(("rec",x))
    for x in parc_home_pend:
        compromissos_home.append(("parc",x))

    if compromissos_home:
        for tipo_comp,x in compromissos_home[:6]:
            if tipo_comp=="conta":
                nome_comp=str(x.get("nome") or "Conta")
                valor_comp=float(x.get("valor") or 0)
                try:
                    venc_comp=pd.to_datetime(x.get("vencimento")).strftime("%d/%m")
                except Exception:
                    venc_comp="—"
                ca,cb=st.columns([4,1])
                ca.markdown(f"🧾 **{nome_comp}** · {money(valor_comp)} · vence {venc_comp}")
                if cb.button("✅ Paguei",key=f"home_pay_conta_{x['id']}",use_container_width=True):
                    sb.table("contas").update({"status":"Pago"}).eq("id",x["id"]).eq("user_id",st.session_state.uid).execute()
                    st.cache_data.clear();st.rerun()
            elif tipo_comp=="rec":
                nome_comp=str(x.get("nome") or "Todo mês")
                valor_comp=float(x.get("valor") or 0)
                dia_comp=int(x.get("dia_vencimento") or 1)
                ca,cb=st.columns([4,1])
                ca.markdown(f"🔁 **{nome_comp}** · {money(valor_comp)} · dia {dia_comp}")
                if cb.button("✅ Paguei",key=f"home_pay_rec_{x['id']}",use_container_width=True):
                    sb.table("recorrentes").update({"ultimo_pago_mes":mes_comp}).eq("id",x["id"]).eq("user_id",st.session_state.uid).execute()
                    sync_recurring_payments();st.cache_data.clear();st.rerun()
            else:
                nome_comp=str(x.get("nome") or "Compra parcelada")
                valor_comp=float(x.get("valor_parcela") or 0)
                total_comp=int(x.get("total_parcelas") or 0)
                pagas_comp=int(x.get("parcelas_pagas") or 0)
                atual_comp=min(pagas_comp+1,total_comp)
                dia_comp=int(x.get("dia_vencimento") or 1)
                ca,cb=st.columns([4,1])
                ca.markdown(f"💳 **{nome_comp}** · parcela **{atual_comp}/{total_comp}** · {money(valor_comp)} · dia {dia_comp}")
                if cb.button("✅ Paguei",key=f"home_pay_parc_{x['id']}",use_container_width=True):
                    novas_pagas=min(pagas_comp+1,total_comp)
                    dados_up={"ultimo_pago_mes":mes_comp,"parcelas_pagas":novas_pagas}
                    if novas_pagas>=total_comp:
                        dados_up["ativo"]=False
                    sb.table("parcelamentos").update(dados_up).eq("id",x["id"]).eq("user_id",st.session_state.uid).execute()
                    sync_installment_payments();st.cache_data.clear()
                    st.session_state["_flash_inicio"]=f"✅ {nome_comp}: parcela {novas_pagas}/{total_comp} paga."
                    st.rerun()
        if len(compromissos_home)>6:
            st.caption(f"+ {len(compromissos_home)-6} compromisso(s). Use os botões abaixo para ver todos.")
    else:
        st.success("✅ Nenhum compromisso pendente neste mês.")

    mc1,mc2,mc3,mc4=st.columns(4)
    if mc1.button("🧾 Contas",use_container_width=True,key="home_contas"):
        st.session_state["_page_destino"]="🧾 Contas"; st.rerun()
    if mc2.button("🔁 Todo mês",use_container_width=True,key="home_recorrentes"):
        st.session_state["_page_destino"]="🔁 Recorrentes"; st.rerun()
    if mc3.button("💳 Parceladas",use_container_width=True,key="home_parcelamentos"):
        st.session_state["_page_destino"]="💳 Parcelamentos"; st.rerun()
    if mc4.button("💳 Meus cartões",use_container_width=True,key="home_cartoes"):
        st.session_state["_page_destino"]="💳 Cartões"; st.rerun()

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


elif page=="⚙️ Minha renda":
    st.title("⚙️ Minha renda")
    st.caption("Conte de forma simples quanto dinheiro entra no mês e quanto você quer separar. O Meu Financeiro faz as contas para você.")
    if st.button("← Voltar ao Início",key="mr_back"):
        st.session_state["_page_destino"]="🏠 Início"
        st.rerun()
    st.divider()
    st.markdown("### Vamos configurar seu mês")
    st.caption("Se você recebe apenas uma vez, deixe a segunda renda em R$ 0,00. Você pode mudar estes valores quando quiser.")
    mr1,mr2=st.columns(2)
    renda1=mr1.number_input("Quanto você recebe normalmente?",min_value=0.0,value=float(cfg["rec1"]),step=100.0,format="%.2f",key="mr_rec1_page")
    renda2=mr2.number_input("Tem outra renda? Quanto?",min_value=0.0,value=float(cfg["rec2"]),step=100.0,format="%.2f",key="mr_rec2_page")
    mr3,mr4=st.columns(2)
    dia2=mr3.number_input("Em que dia recebe essa outra renda?",min_value=1,max_value=28,value=int(cfg["dia2"]),step=1,key="mr_dia2_page")
    pct_guardar=mr4.slider("Quanto quer guardar por mês? (%)",0,50,int(cfg["pct"]),key="mr_pct_page")
    reserva_min=st.number_input("Quanto quer deixar como reserva?",min_value=0.0,value=float(cfg["reserva"]),step=100.0,format="%.2f",key="mr_reserva_page")
    renda_total=renda1+renda2
    st.info(f"💰 Entra no mês: **{money(renda_total)}** · 🐷 Você quer guardar: **{money(renda_total*pct_guardar/100)}** · 🛡️ Reserva: **{money(reserva_min)}**")
    if st.button("💾 Salvar valores",use_container_width=True,key="mr_salvar_page"):
        try:
            sb.table("config").update({
                "rec1":float(renda1),"rec2":float(renda2),"dia2":int(dia2),
                "pct":int(pct_guardar),"reserva":float(reserva_min)
            }).eq("user_id",st.session_state.uid).execute()
            st.cache_data.clear()
            st.session_state["_page_destino"]="🏠 Início"
            st.session_state["_flash_inicio"]="✅ Renda atualizada com sucesso."
            st.rerun()
        except Exception:
            st.error("Não foi possível salvar sua renda.")

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
    renda_pc=float(cfg["rec1"]+cfg["rec2"])
    guardar_pc=renda_pc*float(cfg["pct"])/100
    viver_pc=renda_pc-guardar_pc
    nd_pc,_=nextpay(cfg["dia2"],cfg["rec1"],cfg["rec2"]);dias_pc=max((nd_pc-t).days,0)
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
                        "dia_vencimento":int(pc_primeiro_venc.day),"forma":"Crédito","ativo":True
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
    st.caption("Organize várias contas por titular e deixe tudo preparado para uma futura sincronização via Open Finance.")
    st.info("🔒 O Meu Financeiro nunca deve pedir sua senha bancária, CVV ou número completo do cartão. A futura conexão Open Finance será feita por autorização no ambiente seguro do banco/provedor.")

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

    st.divider();st.subheader("🔗 Open Finance")
    st.write("No futuro, cada conta poderá ser vinculada ao respectivo titular e sincronizada mediante autorização segura no banco/provedor.")
    st.button("Conectar meu banco via Open Finance",use_container_width=True,disabled=True)
    st.caption("Em breve. Nesta versão, os saldos são informados manualmente e não alteram o saldo disponível da tela Início.")

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
