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
