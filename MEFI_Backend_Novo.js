const http = require("http");

const EVOLUTION_URL =
    process.env.EVOLUTION_URL || "http://evolution_api:8080";

const EVOLUTION_API_KEY =
    process.env.EVOLUTION_API_KEY || "";

const SUPABASE_URL = (process.env.SUPABASE_URL || "").replace(/\/$/, "");
const SUPABASE_SECRET_KEY = process.env.SUPABASE_SECRET_KEY || "";

const INSTANCIA = "MEFI";

const pendentes = new Map();

function dinheiro(valor) {
    return Number(valor).toLocaleString("pt-BR", {
        style: "currency",
        currency: "BRL"
    });
}

function normalizarNumero(remoteJid) {
    return String(remoteJid || "")
        .replace("@s.whatsapp.net", "")
        .replace("@lid", "")
        .replace(/\D/g, "");
}

function textoDaMensagem(data) {
    return (
        data.message?.conversation ||
        data.message?.extendedTextMessage?.text ||
        ""
    ).trim();
}

function temAcaoFinanceira(texto) {
    return /\b(gastei|paguei|comprei|abasteci|recebi|recebido|ganhei|vendi|faturei|entrou|saiu)\b/i.test(texto);
}

function interpretar(texto) {
    const t = texto.trim().toLowerCase();
    let tipo = "Saída";

    if (/\b(recebi|recebido|ganhei|entrou|caiu|vendi|faturei)\b/i.test(t))
        tipo = "Entrada";

    if (/\b(gastei|paguei|comprei|saiu|abasteci)\b/i.test(t))
        tipo = "Saída";

    let valor = 0;

    const matchValor =
        t.match(/(?:r\$\s*)?(\d+(?:[.,]\d{1,2})?)/);

    if (matchValor)
        valor = Number(matchValor[1].replace(",", "."));

    let forma = "Não informada";

    if (/\bpix\b/i.test(t))
        forma = "Pix";
    else if (/\bdinheiro\b/i.test(t))
        forma = "Dinheiro";
    else if (/\bdébito\b|\bdebito\b/i.test(t))
        forma = "Débito";
    else if (/\bcrédito\b|\bcredito\b/i.test(t))
        forma = "Crédito";

    let categoria =
        tipo === "Entrada" ? "Renda variável" : "Outros";

    let descricao =
        tipo === "Entrada" ? "Recebimento" : "Gasto";

    if (
        /\bgasolina\b|\bcombustível\b|\bcombustivel\b|\bposto\b|\babastec/i.test(t)
    ) {
        categoria = "Transporte";
        descricao = "Gasolina";

    } else if (/\benergético\b|\benergetico\b/i.test(t)) {

        categoria = "Alimentação";
        descricao = "Energético";

    } else if (/\bmercado\b|\bsupermercado\b/i.test(t)) {

        categoria = "Alimentação";
        descricao = "Mercado";

    } else if (/\bágua\b|\bagua\b/i.test(t)) {

        categoria = "Moradia";
        descricao = "Conta de água";

    } else if (/\bluz\b|\benergia\b/i.test(t)) {

        categoria = "Moradia";
        descricao = "Conta de luz";

    } else if (/\binternet\b/i.test(t)) {

        categoria = "Moradia";
        descricao = "Internet";

    } else if (/\buber\b/i.test(t)) {

        if (tipo === "Entrada") {
            categoria = "Renda variável";
            descricao = "Uber";
        } else {
            categoria = "Transporte";
            descricao = "Uber";
        }

    } else if (
        /\bserviço\b|\bservico\b/i.test(t) &&
        tipo === "Entrada"
    ) {
        categoria = "Renda variável";
        descricao = "Serviço";
    }

    return {
        tipo,
        valor,
        categoria,
        descricao,
        forma
    };
}

async function supabaseRequest(caminho, opcoes = {}) {

    if (!SUPABASE_URL || !SUPABASE_SECRET_KEY)
        throw new Error("Supabase não configurado");

    const resposta = await fetch(
        `${SUPABASE_URL}/rest/v1/${caminho}`,
        {
            ...opcoes,

            headers: {
                "Content-Type": "application/json",
                "apikey": SUPABASE_SECRET_KEY,
                "Prefer":
                    opcoes.prefer ||
                    "return=representation",

                ...(opcoes.headers || {})
            }
        }
    );

    const bruto = await resposta.text();

    let dados = null;

    if (bruto) {
        try {
            dados = JSON.parse(bruto);
        } catch {
            dados = bruto;
        }
    }

    if (!resposta.ok) {
        throw new Error(
            `Supabase ${resposta.status}: ${
                dados?.message ||
                dados ||
                "erro"
            }`
        );
    }

    return dados;
}

function candidatosTelefone(numero) {

    const n =
        String(numero || "")
        .replace(/\D/g, "");

    const candidatos =
        new Set([
            n,
            `+${n}`
        ]);

    if (
        n.startsWith("55") &&
        n.length > 11
    ) {

        const semPais =
            n.slice(2);

        candidatos.add(semPais);
        candidatos.add(`+${semPais}`);
    }

    return [...candidatos]
        .filter(Boolean);
}

async function buscarVinculo(
    numero,
    exigirVerificado = true
) {

    for (
        const tel
        of candidatosTelefone(numero)
    ) {

        const q =
            "whatsapp_vinculos?select=id,user_id,codigo_vinculo,verificado,ativo" +

            `&telefone_e164=${
                encodeURIComponent(
                    "eq." + tel
                )
            }` +

            "&ativo=eq.true" +

            (
                exigirVerificado
                ? "&verificado=eq.true"
                : ""
            ) +

            "&limit=1";

        const dados =
            await supabaseRequest(
                q,
                { method: "GET" }
            );

        if (
            Array.isArray(dados) &&
            dados.length
        )
            return dados[0];
    }

    return null;
}

function extrairCodigo(texto) {

    const match =
        String(texto || "")
        .trim()
        .match(
            /^(?:(?:vincular|codigo|código|mefi)\s*[:#-]?\s*)?([a-z0-9-]{4,32})$/i
        );

    return match
        ? match[1]
        : null;
}

async function tentarVincular(
    numero,
    texto
) {

    const codigo =
        extrairCodigo(texto);

    if (!codigo)
        return false;

    const vinculo =
        await buscarVinculo(
            numero,
            false
        );

    if (
        !vinculo ||
        vinculo.verificado === true
    )
        return false;

    if (
        String(
            vinculo.codigo_vinculo || ""
        )
        .trim()
        .toLowerCase()
        !==
        codigo.toLowerCase()
    )
        return false;

    await supabaseRequest(
        `whatsapp_vinculos?id=eq.${
            encodeURIComponent(
                vinculo.id
            )
        }`,
        {
            method: "PATCH",

            body:
                JSON.stringify({
                    verificado: true
                }),

            prefer:
                "return=minimal"
        }
    );

    await enviarWhatsApp(
        numero,
        [
            "✅ *WhatsApp vinculado ao Meu Financeiro!*",
            "",
            "🤖 O MEFI já pode reconhecer este número.",
            "Agora você pode enviar seus lançamentos financeiros por aqui.",
            "",
            "Exemplo:",
            "_Gastei 25 reais com gasolina no Pix_"
        ].join("\n")
    );

    console.log(
        "✅ WhatsApp verificado e vinculado."
    );

    return true;
}

async function salvarMovimento(
    vinculo,
    movimento
) {

    const payload = {

        user_id:
            vinculo.user_id,

        data:
            new Date()
            .toISOString()
            .slice(0, 10),

        descricao:
            movimento.descricao,

        categoria:
            movimento.categoria,

        tipo:
            movimento.tipo,

        forma:
            movimento.forma,

        valor:
            Number(
                movimento.valor
            ),

        obs:
            "Lançado pelo MEFI via WhatsApp"
    };

    const dados =
        await supabaseRequest(
            "mov",
            {
                method: "POST",

                body:
                    JSON.stringify(
                        payload
                    )
            }
        );

    if (
        !Array.isArray(dados) ||
        !dados.length
    )
        throw new Error(
            "Supabase não confirmou a gravação"
        );

    return dados[0];
}

async function enviarWhatsApp(
    numero,
    texto
) {

    if (!EVOLUTION_API_KEY)
        throw new Error(
            "EVOLUTION_API_KEY não configurada no MEFI Backend"
        );

    const resposta =
        await fetch(
            `${EVOLUTION_URL}/message/sendText/${INSTANCIA}`,
            {
                method: "POST",

                headers: {
                    "Content-Type":
                        "application/json",

                    "apikey":
                        EVOLUTION_API_KEY
                },

                body:
                    JSON.stringify({
                        number: numero,
                        text: texto
                    })
            }
        );

    if (!resposta.ok) {

        const detalhe =
            await resposta.text();

        throw new Error(
            `Evolution respondeu ${resposta.status}: ${detalhe}`
        );
    }

    return resposta.json();
}

function respostaConfirmacao(
    resultado
) {

    const icone =
        resultado.tipo === "Entrada"
        ? "📈"
        : "📉";

    return [
        "🤖 *MEFI entendeu assim:*",
        "",
        `📝 ${resultado.descricao}`,
        `💰 ${dinheiro(resultado.valor)}`,
        `${icone} ${resultado.tipo}`,
        `📂 ${resultado.categoria}`,
        `💳 ${resultado.forma}`,
        "",
        "Está correto?",
        "",
        "Responda:",
        "*CONFIRMAR*",
        "*CORRIGIR*",
        "*CANCELAR*"
    ].join("\n");
}

async function processarMensagem(
    evento
) {

    if (
        evento.event !==
        "messages.upsert"
    )
        return;

    const data =
        evento.data || {};

    const key =
        data.key || {};

    if (
        key.fromMe === true
    )
        return;

    const remoteJid =
        key.remoteJid || "";

    if (
        remoteJid.includes("@g.us") ||
        data.messageType ===
        "reactionMessage"
    )
        return;

    const numero =
        normalizarNumero(
            remoteJid
        );

    const texto =
        textoDaMensagem(
            data
        );

    if (
        !numero ||
        !texto
    )
        return;

    if (
        await tentarVincular(
            numero,
            texto
        )
    )
        return;

    const comando =
        texto
        .trim()
        .toUpperCase();

    const acao =
        comando === "CONFIRMAR" ||
        comando === "CORRIGIR" ||
        comando === "CANCELAR" ||
        temAcaoFinanceira(texto);

    let vinculo = null;

    if (acao) {

        vinculo =
            await buscarVinculo(
                numero,
                true
            );

        if (!vinculo) {

            await enviarWhatsApp(
                numero,
                [
                    "🔒 *Este WhatsApp ainda não está autorizado no Meu Financeiro.*",
                    "",
                    "Abra o Meu Financeiro, acesse a área WhatsApp e conclua a vinculação deste número.",
                    "",
                    "Nenhum dado financeiro foi alterado."
                ].join("\n")
            );

            return;
        }
    }

    if (
        comando ===
        "CONFIRMAR"
    ) {

        const pendente =
            pendentes.get(
                numero
            );

        if (!pendente) {

            await enviarWhatsApp(
                numero,
                "🤖 Não encontrei nenhum lançamento aguardando confirmação."
            );

            return;
        }

        vinculo =
            await buscarVinculo(
                numero,
                true
            );

        if (
            !vinculo ||
            vinculo.user_id !==
            pendente.userId
        ) {

            pendentes.delete(
                numero
            );

            await enviarWhatsApp(
                numero,
                "🔒 Não foi possível confirmar com segurança. Envie o lançamento novamente."
            );

            return;
        }

        try {

            await salvarMovimento(
                vinculo,
                pendente
            );

        } catch (erro) {

            console.error(
                "❌ Falha ao gravar lançamento:",
                erro.message
            );

            await enviarWhatsApp(
                numero,
                "⚠️ Não consegui gravar o lançamento no Meu Financeiro. Nada foi confirmado. Tente *CONFIRMAR* novamente."
            );

            return;
        }

        pendentes.delete(
            numero
        );

        console.log(
            `✅ GRAVADO: ${pendente.descricao} | ${dinheiro(pendente.valor)} | ${pendente.tipo}`
        );

        await enviarWhatsApp(
            numero,
            [
                "✅ *Confirmado e salvo no Meu Financeiro!*",
                "",
                `${pendente.descricao} — ${dinheiro(pendente.valor)}`,
                `${pendente.tipo} · ${pendente.categoria} · ${pendente.forma}`,
                "",
                "📲 O lançamento já foi registrado."
            ].join("\n")
        );

        return;
    }

    if (
        comando ===
        "CANCELAR"
    ) {

        if (
            pendentes.has(
                numero
            )
        ) {

            pendentes.delete(
                numero
            );

            await enviarWhatsApp(
                numero,
                "❌ Lançamento cancelado. Nada foi registrado."
            );

        } else {

            await enviarWhatsApp(
                numero,
                "🤖 Não há lançamento pendente para cancelar."
            );
        }

        return;
    }

    if (
        comando ===
        "CORRIGIR"
    ) {

        if (
            !pendentes.has(
                numero
            )
        ) {

            await enviarWhatsApp(
                numero,
                "🤖 Não encontrei nenhum lançamento aguardando correção."
            );

            return;
        }

        await enviarWhatsApp(
            numero,
            [
                "✏️ Certo.",
                "",
                "Envie novamente a movimentação já corrigida.",
                "",
                "Exemplo:",
                "_Gastei 35 reais com gasolina no Pix_"
            ].join("\n")
        );

        return;
    }

    if (
        !temAcaoFinanceira(
            texto
        )
    ) {

        console.log(
            "💬 Conversa comum ignorada."
        );

        return;
    }

    const resultado =
        interpretar(
            texto
        );

    if (
        !resultado.valor ||
        resultado.valor <= 0
    ) {

        await enviarWhatsApp(
            numero,
            [
                "🤖 Entendi que você está falando de uma movimentação,",
                "mas não consegui identificar o valor.",
                "",
                "Qual foi o valor?",
                "",
                "Exemplo:",
                "_Gastei 25 reais com gasolina no Pix_"
            ].join("\n")
        );

        return;
    }

    pendentes.set(
        numero,
        {
            ...resultado,

            userId:
                vinculo.user_id,

            textoOriginal:
                texto,

            criadoEm:
                new Date()
                .toISOString()
        }
    );

    console.log(
        `🤖 MEFI | ${resultado.tipo} | ${dinheiro(resultado.valor)} | ${resultado.categoria} | aguardando confirmação`
    );

    await enviarWhatsApp(
        numero,
        respostaConfirmacao(
            resultado
        )
    );
}

const server =
    http.createServer(
        (req, res) => {

            if (
                req.method ===
                "POST"
            ) {

                let body = "";

                req.on(
                    "data",
                    chunk => {
                        body +=
                            chunk.toString();
                    }
                );

                req.on(
                    "end",
                    () => {

                        res.writeHead(
                            200,
                            {
                                "Content-Type":
                                    "application/json"
                            }
                        );

                        res.end(
                            JSON.stringify({
                                status:
                                    "recebido"
                            })
                        );

                        try {

                            const evento =
                                JSON.parse(
                                    body
                                );

                            processarMensagem(
                                evento
                            ).catch(
                                erro => {

                                    console.error(
                                        "❌ Erro no MEFI:",
                                        erro.message
                                    );
                                }
                            );

                        } catch (erro) {

                            console.error(
                                "❌ Evento inválido:",
                                erro.message
                            );
                        }
                    }
                );

                return;
            }

            res.writeHead(
                200,
                {
                    "Content-Type":
                        "application/json"
                }
            );

            res.end(
                JSON.stringify({
                    status: "online",
                    service: "MEFI Backend"
                })
            );
        }
    );

server.listen(
    3000,
    "0.0.0.0",
    () => {

        console.log(
            "🤖 MEFI Backend online na porta 3000"
        );
    }
);
