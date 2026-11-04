/* Aba "Transações": fechamentos registrados no ITBI (Prefeitura de SP) × preços pedidos nos anúncios.
 * Usa os utilitários do painel (h, s, $, FMT, store, DATA…) e o motor em itbi.js (window.ITBI). */
"use strict";
let TXJ = null, TXR = null, TXERR = null, TXPAGE = 0;
const TX_DEF = { b: "todos", per: 12, band: -1, basis: "U", out: false, lag: 0, seg: "band", sort: "d", dir: -1, q: "" };
let TX = Object.assign({}, TX_DEF, store.get("tx", {}));
const TX_PERIODS = [[1, "Último mês"], [6, "6 meses"], [12, "12 meses"], [24, "24 meses"]];
const txSave = () => store.set("tx", TX);
const txYm = ym => { const [y, m] = ym.split("-"); return ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"][+m - 1] + "/" + y.slice(2); };
const txPpm = r => TX.basis === "U" ? r.ppmU : r.ppmC;
const txArea = r => TX.basis === "U" ? r.au : r.ac;
const txBasisLbl = () => TX.basis === "U" ? "R$/m² útil equivalente" : "R$/m² construída (ITBI)";
const txAddr = r => `${r.log}${r.num ? ", " + r.num : ""}`;

async function loadTransacoes() {
  if (TXJ || TXERR) return;
  try {
    const r = await fetch("transacoes.json?t=" + Date.now(), { cache: "no-store" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    TXJ = await r.json(); TXR = ITBI.decode(TXJ);
  } catch (e) { TXERR = String(e.message || e); }
}

function txRange() { return ITBI.monthRange(TXJ.parametros.mes_fim, TX.per); }
/** Transações do recorte (bairro, período, faixa); outliers fora, salvo opção. */
function txRows({ ignoreBairro = false, ignoreBand = false, rg = txRange() } = {}) {
  return ITBI.usable(TXR, TX.out).filter(r => ITBI.inRange(r, rg) && (ignoreBairro || TX.b === "todos" || r.b === TX.b)
    && (ignoreBand || TX.band < 0 || ITBI.bandOf(r.au) === TX.band));
}
/** Anúncios de venda ativos (área útil) do bairro e faixa — só bairros monitorados pelo radar. */
function txAsk(bairro, band = TX.band) {
  const L = (DATA?.listings || []).filter(r => r.active && r.business === "venda" && r.area && r.price && (bairro === "todos" ? TXJ.bairros.includes(r.neighborhood) : r.neighborhood === bairro)
    && (band < 0 || ITBI.bandOf(r.area) === band));
  return ITBI.stats(L.map(r => r.price / r.area));
}
const txMonitored = b => (DATA?.criteria?.neighborhoods || []).includes(b);
/** Mediana mensal do R$/m² pedido (retratos diários do histórico do radar). */
function txAskMonthly(bairro) {
  const out = {};
  for (const hd of DATA?.history || []) {
    const g = hd.groups?.["venda|" + bairro]; if (!g?.ppm2) continue;
    (out[hd.date.slice(0, 7)] ||= []).push(g.ppm2);
  }
  return Object.fromEntries(Object.entries(out).map(([k, v]) => [k, median(v)]));
}

/** Desconto comparando imóveis parecidos: por bairro (monitorado) × faixa de área, ponderado pelo nº de transações. */
function txDiscountMix(rows, nbs, band = TX.band) {
  const nMin = TXJ.parametros.n_minimo, f = TXJ.parametros.fator_area_util;
  let w = 0, acc = 0; const cells = [];
  for (const b of nbs.filter(txMonitored)) for (let i = 0; i < ITBI.BANDS.length; i++) {
    if (band >= 0 && i !== band) continue;
    const t = ITBI.stats(rows.filter(r => r.b === b && ITBI.bandOf(r.au) === i).map(txPpm)), a = txAsk(b, i);
    if (t.n < nMin || a.n < nMin) continue;
    const d = ITBI.discount(t.p50, TX.basis === "U" ? a.p50 : a.p50 * f);
    cells.push({ b, i, d, n: t.n }); w += t.n; acc += d * t.n;
  }
  return { d: w ? acc / w : null, n: w, cells };
}

function renderTransacoes() {
  const host = $("#p-transacoes");
  if (!TXJ && !TXERR) { $("#txStatus").replaceChildren(h("div", { class: "empty" }, "Carregando transações…")); loadTransacoes().then(renderTransacoes); return; }
  if (TXERR) { $("#txStatus").replaceChildren(h("div", { class: "empty" }, `Histórico de transações indisponível (${TXERR}). Ele é gerado pelo workflow "Transações (ITBI)".`)); return; }
  $("#txStatus").replaceChildren(); $("#txBody").hidden = false;
  if (!host.dataset.built) { buildTxControls(); host.dataset.built = "1"; }
  syncTxControls();
  renderTxAlerts(); renderTxKpis(); renderTxSeries(); renderTxDisc(); renderTxSeg(); renderTxTable(); renderTxMethod();
  if ($("#txAddr").value) renderTxBuilding(); else $("#txBld").replaceChildren(txBldHint());
  renderTxOffer();
}

function buildTxControls() {
  $("#txBairro").replaceChildren(h("button", { "data-v": "todos" }, "Todos"), ...TXJ.bairros.map(b => h("button", { "data-v": b }, SHORT[b] || b)));
  $("#txPer").replaceChildren(...TX_PERIODS.map(([v, t]) => h("button", { "data-v": v }, t)));
  $("#txBand").replaceChildren(h("option", { value: -1 }, "Todas as áreas"), ...ITBI.BANDS.map(([, , t], i) => h("option", { value: i }, t)));
  $("#txOfNb").replaceChildren(...TXJ.bairros.map(b => h("option", { value: b }, b)));
  const seen = new Set(), dl = $("#txAddrList");
  dl.replaceChildren(...TXR.filter(r => r.num && !seen.has(r.k) && seen.add(r.k)).sort((a, b) => a.log.localeCompare(b.log) || a.num - b.num).map(r => h("option", { value: txAddr(r) })));
  const segs = [["#txBairro", "b", v => v], ["#txPer", "per", Number], ["#txBasis", "basis", v => v]];
  for (const [sel, k, conv] of segs) $(sel).querySelectorAll("button").forEach(b => { b.type = "button"; b.onclick = () => { TX[k] = conv(b.dataset.v); TXPAGE = 0; txSave(); renderTransacoes(); }; });
  $("#txBand").onchange = e => { TX.band = +e.target.value; TXPAGE = 0; txSave(); renderTransacoes(); };
  $("#txOut").onchange = e => { TX.out = e.target.checked; TXPAGE = 0; txSave(); renderTransacoes(); };
  $("#txLag").onchange = e => { TX.lag = +e.target.value; txSave(); renderTxDisc(); };
  $("#txSegSel").onchange = e => { TX.seg = e.target.value; txSave(); renderTxSeg(); };
  let t1; $("#txQ").oninput = e => { clearTimeout(t1); t1 = setTimeout(() => { TX.q = e.target.value; TXPAGE = 0; txSave(); renderTxTable(); }, 200); };
  let t2; $("#txAddr").oninput = () => { clearTimeout(t2); t2 = setTimeout(renderTxBuilding, 250); };
  $("#txAddrClear").onclick = () => { $("#txAddr").value = ""; $("#txBld").replaceChildren(txBldHint()); renderTxOffer(); };
  let t3; $("#txOffer").addEventListener("input", () => { clearTimeout(t3); t3 = setTimeout(renderTxOffer, 200); });
  $("#txOffer").addEventListener("change", renderTxOffer);
  $("#txCsv").onclick = txCsv;
}
function syncTxControls() {
  const press = (sel, v) => $(sel).querySelectorAll("button").forEach(b => b.setAttribute("aria-pressed", String(String(b.dataset.v) === String(v))));
  press("#txBairro", TX.b); press("#txPer", TX.per); press("#txBasis", TX.basis);
  $("#txBand").value = TX.band; $("#txOut").checked = TX.out; $("#txLag").value = TX.lag; $("#txSegSel").value = TX.seg; $("#txQ").value = TX.q;
  const rg = txRange();
  $("#txSub").textContent = `${txYm(rg.from)} a ${txYm(rg.to)} (mês da transação) · apartamentos, compra e venda, 100% transmitido · ${txBasisLbl()}`;
}

function renderTxAlerts() {
  const q = TXJ.qualidade, out = [];
  out.push(["info", `Valores declarados ao fisco na guia de ITBI, não necessariamente o preço negociado. Área do ITBI = área construída do IPTU (inclui áreas comuns); "útil equivalente" = construída × ${String(TXJ.parametros.fator_area_util).replace(".", ",")}.`]);
  if (TX.per === 1 || txRange().to === q.ultimo_mes_incompleto) out.push(["warn", `${txYm(q.ultimo_mes_incompleto)} é o mês mais recente publicado e costuma chegar incompleto: parte das guias é paga no mês seguinte.`]);
  for (const a of q.alertas || []) out.push(["warn", "Checagem dos dados: " + a]);
  $("#txAlerts").replaceChildren(...out.map(([lvl, t]) => h("div", { class: "alert " + lvl }, h("span", { class: "ico " + (lvl === "info" ? "" : lvl), style: lvl === "info" ? "background:var(--accent)" : null, "aria-hidden": "true" }, lvl === "info" ? "i" : "!"), h("span", {}, t))));
}

function renderTxKpis() {
  const rows = txRows(), s = ITBI.stats(rows.map(txPpm)), rg = txRange();
  const nbs = TX.b === "todos" ? TXJ.bairros : [TX.b];
  const ask = txAsk(TX.b), monAll = nbs.every(txMonitored), monAny = nbs.some(txMonitored);
  const askBasis = TX.basis === "U" ? ask.p50 : ask.p50 && ask.p50 * TXJ.parametros.fator_area_util;
  const closed = TX.basis === "U" ? s.p50 : s.p50;
  const mix = txDiscountMix(rows, nbs), disc = mix.d;
  const kp = (label, value, unit, per, cmp) => h("div", { class: "card kpi" }, h("div", { class: "label" }, label), h("div", { class: "value" }, value, unit ? h("span", { class: "unit" }, " " + unit) : null), h("div", { class: "per" }, per), cmp ? h("div", { class: "cmp" }, cmp) : null);
  const vol = rows.reduce((a, r) => a + r.v, 0);
  $("#txKpis").replaceChildren(
    kp("Transações", FMT.int(s.n), "", `${txYm(rg.from)}–${txYm(rg.to)}${TX.band >= 0 ? " · " + ITBI.BANDS[TX.band][2] : ""}`, `volume declarado ${FMT.brlc(vol)}`),
    kp("Fechamento (mediana)", s.n ? FMT.brl(s.p50) : "—", "/m²", txBasisLbl(), s.n >= 4 ? `metade entre ${FMT.brl(s.p25)} e ${FMT.brl(s.p75)}` : "amostra pequena"),
    kp("Pedido nos anúncios (mediana)", monAny && ask.n ? FMT.brl(askBasis) : "—", "/m²", monAny ? `anúncios ativos hoje · n = ${FMT.int(ask.n)}${TX.basis === "C" ? " · convertido p/ área construída" : ""}` : "bairro fora do radar de anúncios",
      monAny && !monAll ? "só bairros monitorados: " + nbs.filter(txMonitored).map(b => SHORT[b] || b).join(", ") : ""),
    kp("Desconto implícito", disc != null ? FMT.pct(disc, 0) : "—", "", "mediana de fechamento ÷ pedido − 1", disc != null ? `por bairro e faixa de área (${mix.cells.length} combinações, ${FMT.int(mix.n)} transações)` : "sem anúncios comparáveis no recorte"),
    kp("Dados até", txYm(TXJ.parametros.mes_fim), "", `atualizado ${FMT.dateTime(TXJ.atualizado)}`, `${FMT.int(TXR.length)} transações em ${TXJ.parametros.janela_meses} meses`));
}

/* ---------- busca por endereço / prédio ---------- */
function txBldHint() { return h("p", { class: "note" }, "Digite rua e número (ex.: “Rua Joaquim Floriano, 100”). Sem número, aparece a lista de prédios da rua com transações no período."); }
function renderTxBuilding() {
  const box = $("#txBld"), a = ITBI.parseAddress($("#txAddr").value);
  if (!a.skey) { box.replaceChildren(txBldHint()); renderTxOffer(); return; }
  const rg = txRange(), all = ITBI.usable(TXR, TX.out);
  if (!a.number) {
    const by = {};
    for (const r of all.filter(r => r.sk.includes(a.skey) && ITBI.inRange(r, rg))) (by[r.k] ||= []).push(r);
    const list = Object.values(by).sort((x, y) => y.length - x.length).slice(0, 15);
    box.replaceChildren(list.length ? h("div", {}, h("div", { class: "sub" }, `Prédios com transações no período (${list.length}${Object.keys(by).length > 15 ? " de " + Object.keys(by).length : ""}):`),
      h("ul", { class: "bldlist" }, list.map(rs => h("li", {}, h("button", { class: "linkbtn", type: "button", onclick: () => { $("#txAddr").value = txAddr(rs[0]); renderTxBuilding(); } }, txAddr(rs[0])),
        h("span", { class: "muted" }, ` · ${rs.length} transaç${rs.length > 1 ? "ões" : "ão"} · mediana ${FMT.brl(median(rs.map(txPpm)))}/m² · ${rs[0].b}`)))))
      : h("div", { class: "empty" }, "Nenhuma transação nessa rua no período (confira a grafia ou aumente o período)."));
    renderTxOffer(); return;
  }
  const rs = all.filter(r => r.k === a.key), inP = rs.filter(r => ITBI.inRange(r, rg));
  const asks = (DATA?.listings || []).filter(l => l.active && l.business === "venda" && l.address && ITBI.parseAddress(l.address).key === a.key);
  if (!rs.length) {
    box.replaceChildren(h("div", { class: "empty" }, `Nenhuma transação registrada em ${a.street}, ${a.number} nos últimos ${TXJ.parametros.janela_meses} meses (ou o endereço está fora dos bairros acompanhados).`), asks.length ? txAskList(asks, null) : "");
    renderTxOffer(); return;
  }
  const use = inP.length ? inP : rs, s = ITBI.stats(use.map(txPpm));
  box.replaceChildren(
    h("div", { class: "bldhead" }, h("b", {}, txAddr(rs[0])), h("span", { class: "muted" }, ` · ${rs[0].b} · CEP ${rs[0].cep.replace(/(\d{5})(\d{3})/, "$1-$2")}${rs[0].acc ? " · construído em " + rs[0].acc : ""}${rs[0].pad ? " · padrão IPTU " + rs[0].pad : ""}`)),
    h("div", { class: "summary", style: "margin:6px 0" },
      h("span", {}, h("b", {}, FMT.int(use.length)), inP.length ? ` transações no período (${txYm(rg.from)}–${txYm(rg.to)})` : ` transações em ${TXJ.parametros.janela_meses} meses (nenhuma no período escolhido)`),
      h("span", {}, "Mediana ", h("b", {}, FMT.brl(s.p50) + "/m²"), use.length >= 4 ? ` (P25–P75: ${FMT.brl(s.p25)}–${FMT.brl(s.p75)})` : ""),
      h("span", {}, "Área útil eq. mediana ", h("b", {}, FMT.m2(median(use.map(r => r.au)))))),
    h("div", { class: "tablewrap" }, h("table", { class: "data" },
      h("thead", {}, h("tr", {}, ...["Data", "Unidade", "Área útil eq.", "Área constr.", "Valor declarado", txBasisLbl().replace("R$/m² ", "R$/m² ")].map((t, i) => h("th", { class: i > 1 ? "num" : null }, t)), h("th", {}, ""))),
      h("tbody", {}, use.slice(0, 40).map(r => h("tr", {}, h("td", {}, FMT.date(r.d) + "/" + r.d.slice(2, 4)), h("td", {}, r.cpl || "—"), h("td", { class: "num" }, nf0.format(r.au)), h("td", { class: "num" }, nf0.format(r.ac)),
        h("td", { class: "num" }, nf0.format(r.v)), h("td", { class: "num" }, nf0.format(txPpm(r))), h("td", {}, txFlags(r))))))),
    asks.length ? txAskList(asks, s.p50) : h("p", { class: "note" }, "Nenhum anúncio ativo do radar neste endereço (ZAP e Viva Real nem sempre informam o número)."));
  renderTxOffer();
}
function txAskList(asks, closedU) {
  const f = TXJ.parametros.fator_area_util;
  return h("div", { style: "margin-top:8px" }, h("div", { class: "sub" }, `Anúncios ativos no mesmo endereço (${asks.length}) — pedido × fechamentos do prédio:`),
    h("ul", { class: "bldlist" }, asks.slice(0, 8).map(l => { const ppm = l.price / l.area, u = safeUrl(l.url), cmpv = closedU != null ? (TX.basis === "U" ? closedU : closedU / f) : null;
      return h("li", {}, u ? h("a", { href: u, target: "_blank", rel: "noopener" }, `${FMT.brlc(l.price)} · ${FMT.m2(l.area)} ↗`) : `${FMT.brlc(l.price)} · ${FMT.m2(l.area)}`,
        h("span", { class: "muted" }, ` · ${FMT.brl(ppm)}/m² útil${cmpv ? ` · fechamentos ${FMT.pct(cmpv / ppm - 1, 0)} vs. pedido` : ""}`)); })));
}
const txFlags = r => h("span", { class: "txflags" }, ...[["o", "fora da faixa"], ["l", "em bloco"], ["v", "< venal"], ["e", "= venal"], ["n", "novo"]].filter(([k]) => r.flags.includes(k))
  .map(([k, t]) => h("span", { class: "badge" + (k === "o" || k === "l" ? " gone" : ""), title: TXJ.flags[k] }, t)), r.fin ? h("span", { class: "badge", title: "Com financiamento" }, "financ.") : null);

/* ---------- calculadora de oferta ---------- */
function renderTxOffer() {
  const price = parseBR($("#txOfPrice").value), area = parseBR($("#txOfArea").value);
  const a = ITBI.parseAddress($("#txAddr").value);
  const rows = ITBI.usable(TXR, TX.out).filter(r => ITBI.inRange(r, txRange()));
  const bldRow = a.number ? TXR.find(r => r.k === a.key) : null;
  if (bldRow && !$("#txOfNb").dataset.touched) $("#txOfNb").value = bldRow.b;
  const nb = $("#txOfNb").value;
  const o = ITBI.offer({ price: isFinite(price) ? price : null, area: isFinite(area) ? area : null, key: a.number ? a.key : null, bairro: nb }, rows, TXJ.parametros.n_minimo);
  const box = $("#txOfOut");
  if (!isFinite(area) || area <= 0) { box.replaceChildren(h("p", { class: "note" }, "Informe a área útil (m²) do anúncio e o preço pedido. Com um endereço na busca ao lado, entram os fechamentos daquele prédio.")); return; }
  const line = (t, v, sub) => h("div", { class: "ofl" }, h("span", {}, t), h("b", {}, v), sub ? h("span", { class: "muted" }, sub) : null);
  const per = `${txYm(txRange().from)}–${txYm(txRange().to)}`;
  box.replaceChildren(
    o.ask ? line("Pedido", FMT.brl(o.ask) + "/m² útil", `${FMT.brlc(price)} ÷ ${FMT.m2(area)}`) : null,
    line("Fechamentos no prédio", o.bld.n ? FMT.brl(o.bld.p50) + "/m²" : "—", o.bld.n ? `${o.bld.n} transaç${o.bld.n > 1 ? "ões" : "ão"} · ${per}${o.bld.n < 3 ? " · poucas para referência" : ""}` : a.number ? "nenhuma no período" : "informe o endereço na busca"),
    line(`${nb}, ${o.band >= 0 ? ITBI.BANDS[o.band][2] : "todas as áreas"}`, o.nb.n >= TXJ.parametros.n_minimo ? FMT.brl(o.nb.p50) + "/m²" : "—", o.nb.n >= TXJ.parametros.n_minimo ? `P25–P75: ${FMT.brl(o.nb.p25)}–${FMT.brl(o.nb.p75)} · n = ${o.nb.n}` : `amostra pequena (n = ${o.nb.n})`),
    o.ref ? h("div", { class: "ofres" },
      h("div", {}, "Referência: ", h("b", {}, o.ref.kind), o.ask ? [" · desconto implícito ", h("b", { class: o.disc < 0 ? "neg" : "" }, FMT.pct(o.disc, 0))] : ""),
      h("div", {}, `Para ${FMT.m2(area)}, os fechamentos indicam `, h("b", {}, `${FMT.brlc(o.range[0])} a ${FMT.brlc(o.range[2])}`), ` (mediana ${FMT.brlc(o.range[1])}).`),
      o.ask && o.disc < -0.25 ? h("div", { class: "muted" }, "Diferença grande: confira se o anúncio é de unidade diferente (andar, reforma, vagas) ou se as transações do prédio são antigas.") : null)
      : h("p", { class: "note" }, "Sem transações suficientes neste recorte para uma referência."),
    h("p", { class: "note" }, "Área útil equivalente = área construída do ITBI × fator. Valores declarados ao fisco. É uma referência para negociação, não uma avaliação."));
}

/* ---------- série mensal por bairro ---------- */
function renderTxSeries() {
  const host = $("#txSeries"); host.replaceChildren();
  const nbs = TX.b === "todos" ? TXJ.bairros : [TX.b], end = TXJ.parametros.mes_fim;
  const now = (DATA?.updated_at || "").slice(0, 7), to = now > end ? now : end;   // estende até hoje para mostrar o pedido atual
  const rg = { from: ITBI.addMonths(end, -(TXJ.parametros.janela_meses - 1)), to };
  const grid = h("div", { class: "grid" }); host.append(grid);
  for (const b of nbs) {
    const rows = ITBI.usable(TXR, TX.out).filter(r => r.b === b && (TX.band < 0 || ITBI.bandOf(r.au) === TX.band));
    const ser = ITBI.monthly(rows, rg, TX.basis), ask = txMonitored(b) ? txAskMonthly(b) : {};
    const ch = h("div", { class: "chart" });
    grid.append(h("div", { class: nbs.length === 1 ? "c12" : nbs.length <= 2 ? "c6" : "c4" }, h("div", { class: "ink2", style: "font-size:12.5px;font-weight:600" }, b, h("span", { class: "muted", style: "font-weight:400" }, ` · ${FMT.int(rows.filter(r => ITBI.inRange(r, rg)).length)} transações`)), ch));
    txBandChart(ch, ser, ask, b, nbs.length === 1 ? 260 : 170);
  }
  $("#txSeriesSub").textContent = `${txBasisLbl()} · mês da transação · ${TXJ.parametros.janela_meses} meses · faixa = metade central (P25–P75); meses com menos de ${TXJ.parametros.n_minimo} transações sem faixa${TX.band >= 0 ? " · " + ITBI.BANDS[TX.band][2] : ""} · pedido: radar de anúncios desde out/26 (só Itaim Bibi, Vila Nova Conceição e Jardim Paulista), ITBI até ${txYm(end)}`;
}
function txBandChart(host, ser, ask, title, H) {
  const W = host.clientWidth || 360, m = { t: 14, r: 10, b: 22, l: 48 }, nMin = TXJ.parametros.n_minimo, f = TXJ.parametros.fator_area_util;
  const askV = ym => ask[ym] != null ? (TX.basis === "U" ? ask[ym] : ask[ym] * f) : null;
  const ok = ser.filter(p => p.n >= nMin), vals = ok.flatMap(p => [p.p25, p.p75]).concat(ser.map(p => askV(p.ym)).filter(v => v != null));
  if (!ok.length) { host.append(h("div", { class: "empty" }, "Poucas transações neste recorte.")); return; }
  const yt = niceTicks(Math.min(...vals) * .9, Math.max(...vals) * 1.05, 4), y0 = yt[0], y1 = yt.at(-1);
  const X = i => m.l + i / Math.max(1, ser.length - 1) * (W - m.l - m.r), Y = v => m.t + (1 - (v - y0) / (y1 - y0)) * (H - m.t - m.b);
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label": `R$/m² de fechamento por mês, ${title}` });
  for (const t of yt) { svg.append(s("line", { class: "gridl", x1: m.l, x2: W - m.r, y1: Y(t), y2: Y(t) })); svg.append(s("text", { x: m.l - 5, y: Y(t) + 4, "text-anchor": "end" }, FMT.axis(t))); }
  const step = Math.ceil(ser.length / Math.max(2, Math.floor((W - m.l) / 52)));
  ser.forEach((p, i) => { const last = i === ser.length - 1; if ((i % step === 0 && ser.length - 1 - i >= step * .7) || last) svg.append(s("text", { x: X(i), y: H - 6, "text-anchor": i === 0 ? "start" : i === ser.length - 1 ? "end" : "middle" }, txYm(p.ym))); });
  // faixa P25–P75 por trechos contínuos de meses com amostra suficiente
  let seg = [];
  const flush = () => { if (seg.length > 1) svg.append(s("path", { d: seg.map(([i, p], k) => (k ? "L" : "M") + X(i) + " " + Y(p.p75)).join("") + seg.slice().reverse().map(([i, p]) => "L" + X(i) + " " + Y(p.p25)).join("") + "Z", fill: "var(--s1)", opacity: .16 })); seg = []; };
  ser.forEach((p, i) => { if (p.n >= nMin) seg.push([i, p]); else flush(); }); flush();
  let d = "", prev = false;
  ser.forEach((p, i) => { if (p.n >= nMin) { d += (prev ? "L" : "M") + X(i) + " " + Y(p.p50); prev = true; } else prev = false; });
  svg.append(s("path", { d, fill: "none", stroke: "var(--s1)", "stroke-width": 2 }));
  ser.forEach((p, i) => { if (p.n >= nMin) svg.append(s("circle", { cx: X(i), cy: Y(p.p50), r: 2.2, fill: "var(--s1)" })); });
  let da = "", pa = false;
  ser.forEach((p, i) => { const v = askV(p.ym); if (v != null) { da += (pa ? "L" : "M") + X(i) + " " + Y(v); pa = true; } else pa = false; });
  if (da) { svg.append(s("path", { d: da, fill: "none", stroke: "var(--ink-2)", "stroke-width": 2, "stroke-dasharray": "5 4" }));
    ser.forEach((p, i) => { const v = askV(p.ym); if (v != null) svg.append(s("circle", { cx: X(i), cy: Y(v), r: 2.6, fill: "var(--ink-2)" })); }); }
  const cross = s("line", { y1: m.t, y2: H - m.b, stroke: "var(--axis)", opacity: 0 }); svg.append(cross);
  const hit = s("rect", { x: m.l, y: 0, width: W - m.l - m.r, height: H, fill: "transparent" });
  hit.addEventListener("pointermove", e => { const rc = svg.getBoundingClientRect(), xv = (e.clientX - rc.left) * (W / rc.width);
    const i = Math.max(0, Math.min(ser.length - 1, Math.round((xv - m.l) / ((W - m.l - m.r) / Math.max(1, ser.length - 1))))), p = ser[i], av = askV(p.ym);
    cross.setAttribute("x1", X(i)); cross.setAttribute("x2", X(i)); cross.setAttribute("opacity", 1);
    showTip(h("div", {}, h("div", { class: "tl" }, `${title} · ${txYm(p.ym)}`),
      h("div", { class: "row" }, h("span", { class: "tl" }, "Fechamento (mediana)"), h("span", { class: "tv" }, p.n ? FMT.brl(p.p50) + "/m²" : "—")),
      p.n >= 4 ? h("div", { class: "tl" }, `P25–P75: ${FMT.brl(p.p25)}–${FMT.brl(p.p75)}`) : null,
      h("div", { class: "tl" }, `${p.n} transaç${p.n === 1 ? "ão" : "ões"}${p.n < nMin ? " · amostra pequena" : ""}`),
      av != null ? h("div", { class: "row" }, h("span", { class: "tl" }, "Pedido (anúncios)"), h("span", {}, FMT.brl(av) + "/m²")) : null), X(i), m.t + 10, host); });
  hit.addEventListener("pointerleave", () => { cross.setAttribute("opacity", 0); hideTip(); });
  svg.append(hit); host.append(svg);
}

/* ---------- desconto implícito ao longo do tempo ---------- */
function renderTxDisc() {
  const host = $("#txDisc"); host.replaceChildren();
  const rg = ITBI.monthRange(TXJ.parametros.mes_fim, TXJ.parametros.janela_meses), nMin = TXJ.parametros.n_minimo;
  const nbs = (TX.b === "todos" ? TXJ.bairros : [TX.b]).filter(txMonitored), f = TXJ.parametros.fator_area_util;
  const pts = [];
  for (const b of nbs) {
    const ask = txAskMonthly(b), ser = ITBI.monthly(ITBI.usable(TXR, TX.out).filter(r => r.b === b && (TX.band < 0 || ITBI.bandOf(r.au) === TX.band)), rg, "U");
    for (const p of ser) { const a = ask[ITBI.addMonths(p.ym, -TX.lag)]; if (p.n >= nMin && a) pts.push({ b, ym: p.ym, d: p.p50 / a - 1, n: p.n }); }
  }
  const first = Object.keys(txAskMonthly(nbs[0] || "")).sort()[0];
  $("#txDiscSub").textContent = `Mediana de fechamento (útil eq.) do mês ÷ mediana pedida ${TX.lag ? TX.lag + " meses antes" : "no mesmo mês"} − 1 · bairros monitorados pelo radar`;
  if (!nbs.length) { host.append(h("div", { class: "empty" }, "Os anúncios só são monitorados em " + FMT.list(DATA.criteria.neighborhoods) + ".")); return; }
  if (!pts.length) {
    const firstM = first ? ITBI.addMonths(first.slice(0, 7), TX.lag) : null;
    host.append(h("div", { class: "empty" }, `Ainda sem meses em comum: o radar registra anúncios desde ${first ? FMT.date(first + "-01T12:00") + "/" + first.slice(2, 4) : "—"} e o ITBI publicado vai até ${txYm(TXJ.parametros.mes_fim)}. ` +
      (firstM ? `O 1º ponto aparece quando o ITBI de ${txYm(firstM)} for publicado (cerca de 1–2 meses depois).` : "")));
    host.append(h("p", { class: "note" }, "Enquanto isso, o desconto contra os anúncios de hoje está no indicador acima e na tabela por segmento."));
    return;
  }
  const W = host.clientWidth || 500, H = 220, m = { t: 14, r: 10, b: 24, l: 44 };
  const months = [...new Set(pts.map(p => p.ym))].sort(), vals = pts.map(p => p.d).concat([0]);
  const yt = niceTicks(Math.min(...vals) * 1.1, Math.max(0, ...vals) * 1.1 + .01, 4), y0 = yt[0], y1 = yt.at(-1);
  const bw = (W - m.l - m.r) / months.length, X = i => m.l + i * bw, Y = v => m.t + (1 - (v - y0) / (y1 - y0)) * (H - m.t - m.b);
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label": "Desconto implícito por mês" });
  for (const t of yt) { svg.append(s("line", { class: t === 0 ? "base" : "gridl", x1: m.l, x2: W - m.r, y1: Y(t), y2: Y(t) })); svg.append(s("text", { x: m.l - 5, y: Y(t) + 4, "text-anchor": "end" }, FMT.pct(t, 0))); }
  months.forEach((ym, i) => {
    const ps = pts.filter(p => p.ym === ym), w = bw * .8 / nbs.length;
    ps.forEach(p => { const k = nbs.indexOf(p.b), x = X(i) + bw * .1 + k * w;
      const r = s("rect", { x, y: Math.min(Y(0), Y(p.d)), width: Math.max(1, w - 1), height: Math.abs(Y(p.d) - Y(0)), fill: COLOR[p.b] || "var(--s1)", rx: 1.5 });
      r.append(s("title", {}, `${p.b} · ${txYm(ym)}: ${FMT.pct(p.d, 1)} (${p.n} transações)`)); svg.append(r); });
    svg.append(s("text", { x: X(i) + bw / 2, y: H - 6, "text-anchor": "middle" }, txYm(ym)));
  });
  host.append(svg, h("div", { class: "legend", style: "margin-top:4px" }, ...nbs.map(b => h("span", {}, h("span", { class: "sw", style: `background:${COLOR[b]}` }), SHORT[b] || b))));
}

/* ---------- segmentos ---------- */
function renderTxSeg() {
  const rows = txRows({ ignoreBairro: true, ignoreBand: TX.seg === "band" }), nMin = TXJ.parametros.n_minimo, f = TXJ.parametros.fator_area_util;
  const year = +TXJ.parametros.mes_fim.slice(0, 4);
  const defs = { band: ITBI.BANDS.map(b => b[2]), pad: ["A", "B", "C", "D", "E"].map(p => "Padrão " + p), age: ITBI.AGES.map(a => a[2]) };
  const keyOf = { band: r => ITBI.bandOf(r.au), pad: r => "ABCDE".indexOf(r.pad || "?"), age: r => ITBI.ageOf(r.acc, +r.d.slice(0, 4)) }[TX.seg];
  const cols = defs[TX.seg], nbs = TXJ.bairros;
  $("#txSegSub").textContent = `${txBasisLbl()} mediano · ${txYm(txRange().from)}–${txYm(txRange().to)} · células com menos de ${nMin} transações ocultas` + (TX.seg === "band" ? " · desconto vs. anúncios ativos de hoje na mesma faixa (área útil)" : " · os anúncios não informam padrão nem idade: sem desconto");
  $("#txSeg").replaceChildren(h("div", { class: "tablewrap" }, h("table", { class: "data" },
    h("thead", {}, h("tr", {}, h("th", {}, "Bairro"), ...cols.map(c => h("th", { class: "num" }, c)))),
    h("tbody", {}, nbs.map(b => h("tr", {}, h("td", {}, b), ...cols.map((c, i) => {
      const st = ITBI.stats(rows.filter(r => r.b === b && keyOf(r) === i).map(txPpm));
      if (st.n < nMin) return h("td", { class: "num muted", title: `n = ${st.n}` }, st.n ? `n = ${st.n}` : "—");
      let disc = null;
      if (TX.seg === "band" && txMonitored(b)) { const a = txAsk(b, i); if (a.n >= nMin) disc = ITBI.discount(st.p50, TX.basis === "U" ? a.p50 : a.p50 * f); }
      return h("td", { class: "num" }, h("div", {}, FMT.brl(st.p50)), h("div", { class: "muted", style: "font-size:11px" }, `n = ${st.n}${disc != null ? " · " + FMT.pct(disc, 0) + " vs. pedido" : ""}`));
    })))))));
}

/* ---------- tabela ---------- */
const TX_COLS = [["d", "Data", r => r.d], ["addr", "Endereço", r => r.log + " " + String(r.num).padStart(5, "0")], ["cpl", "Unidade", r => r.cpl], ["b", "Bairro", r => r.b],
  ["au", "Área útil eq.", r => r.au, "m²"], ["ac", "Área constr.", r => r.ac, "m² (ITBI)"], ["v", "Valor declarado", r => r.v, "R$"], ["ppm", "R$/m²", r => txPpm(r), () => TX.basis === "U" ? "útil eq." : "construída"],
  ["acc", "Ano constr.", r => r.acc || 0, "ACC (IPTU)"], ["pad", "Padrão", r => r.pad || ""], ["fl", "Sinais", r => r.flags]];
function txTableRows() {
  const q = ITBI.plain(TX.q);
  let rows = txRows();
  if (q) rows = rows.filter(r => ITBI.plain(`${r.log} ${r.num} ${r.cpl} ${r.b} ${r.cep}`).includes(q));
  const c = TX_COLS.find(c => c[0] === TX.sort) || TX_COLS[0];
  return rows.sort((a, b) => { const x = c[2](a), y = c[2](b); return (x < y ? -1 : x > y ? 1 : 0) * TX.dir; });
}
function renderTxTable() {
  const rows = txTableRows(), PS = 50, pages = Math.max(1, Math.ceil(rows.length / PS)); TXPAGE = Math.min(TXPAGE, pages - 1);
  const view = rows.slice(TXPAGE * PS, (TXPAGE + 1) * PS), num = new Set(["au", "ac", "v", "ppm", "acc"]);
  const u = c => typeof c[3] === "function" ? c[3]() : c[3];
  $("#txTblSum").replaceChildren(h("span", {}, h("b", {}, FMT.int(rows.length)), " transações no filtro"), h("span", {}, "Mediana: ", h("b", {}, FMT.brl(median(rows.map(txPpm))) + "/m²")), h("span", {}, "Valor mediano: ", h("b", {}, FMT.brlc(median(rows.map(r => r.v))))));
  $("#txTbl").replaceChildren(
    h("thead", {}, h("tr", {}, TX_COLS.map(c => h("th", { class: num.has(c[0]) ? "num" : null, "aria-sort": TX.sort === c[0] ? (TX.dir > 0 ? "ascending" : "descending") : "none" },
      h("button", { class: "sortbtn", type: "button", onclick: () => { TX.dir = TX.sort === c[0] ? -TX.dir : (num.has(c[0]) || c[0] === "d" ? -1 : 1); TX.sort = c[0]; TXPAGE = 0; txSave(); renderTxTable(); } }, c[1]), u(c) ? h("span", { class: "u" }, u(c)) : null)))),
    h("tbody", {}, view.length ? view.map(r => h("tr", { class: /[ol]/.test(r.flags) ? "inactive" : null },
      h("td", {}, r.d.split("-").reverse().join("/")), h("td", {}, h("button", { class: "linkbtn", type: "button", title: "Ver o histórico deste prédio", onclick: () => { $("#txAddr").value = txAddr(r); renderTxBuilding(); $("#txAddr").scrollIntoView({ behavior: "smooth", block: "center" }); } }, txAddr(r))),
      h("td", {}, r.cpl || "—"), h("td", {}, SHORT[r.b] || r.b), h("td", { class: "num" }, nf0.format(r.au)), h("td", { class: "num" }, nf0.format(r.ac)), h("td", { class: "num" }, nf0.format(r.v)),
      h("td", { class: "num" }, nf0.format(txPpm(r))), h("td", { class: "num" }, r.acc || "—"), h("td", {}, r.pad || "—"), h("td", {}, txFlags(r))))
      : h("tr", {}, h("td", { colspan: TX_COLS.length }, h("div", { class: "empty" }, "Nenhuma transação com esses filtros.")))));
  $("#txPager").replaceChildren(h("span", {}, rows.length ? `${FMT.int(TXPAGE * PS + 1)}–${FMT.int(Math.min(rows.length, (TXPAGE + 1) * PS))} de ${FMT.int(rows.length)}` : ""),
    h("span", { class: "card-tools" }, h("button", { class: "btn sm", type: "button", disabled: TXPAGE === 0 || null, onclick: () => { TXPAGE--; renderTxTable(); } }, "← Anterior"), h("span", {}, `pág. ${TXPAGE + 1}/${pages}`),
      h("button", { class: "btn sm", type: "button", disabled: TXPAGE >= pages - 1 || null, onclick: () => { TXPAGE++; renderTxTable(); } }, "Próxima →")));
}
function txCsv() {
  const rows = txTableRows(), f = TXJ.parametros.fator_area_util;
  csvDownload([["Data da transação", "Logradouro", "Número", "Unidade (complemento)", "Bairro", "CEP", "Área construída ITBI (m²)", `Área útil equivalente (m², × ${String(f).replace(".", ",")})`,
    "Valor declarado (R$)", "Valor venal de referência (R$)", "R$/m² construída", "R$/m² útil equivalente", "Financiado", "Ano de construção (ACC)", "Padrão IPTU", "Sinais"],
    ...rows.map(r => [r.d, r.log, r.num, r.cpl, r.b, r.cep, r.ac, Math.round(r.au), r.v, r.vv, Math.round(r.ppmC), Math.round(r.ppmU), r.fin ? "sim" : "não", r.acc, r.pad,
      [...r.flags].map(k => TXJ.flags[k]).join("; ")])],
    `Transações ITBI — ${TX.b === "todos" ? "todos os bairros" : TX.b} — ${txYm(txRange().from)} a ${txYm(txRange().to)}`, "radar-transacoes",
    { updated: TXJ.atualizado, filters: [TX.b === "todos" ? "todos os bairros" : TX.b, `${txYm(txRange().from)}–${txYm(txRange().to)}`, TX.band >= 0 ? ITBI.BANDS[TX.band][2] + " (útil eq.)" : "todas as áreas",
      TX.out ? "com outliers e compras em bloco" : "sem outliers nem compras em bloco", TX.q ? `busca “${TX.q}”` : ""].filter(Boolean).join(" · "),
      notes: `Notas: ${TXJ.fonte}. Valor declarado na guia (não necessariamente o negociado). Área construída do IPTU inclui áreas comuns; útil equivalente = construída × ${String(f).replace(".", ",")}. Data = data da transação.` });
}

/* ---------- metodologia ---------- */
function renderTxMethod() {
  const p = TXJ.parametros, q = TXJ.qualidade, safe = safeUrl(TXJ.pagina);
  $("#txMethod").replaceChildren(
    h("dl", { class: "defs" },
      h("dt", {}, "Fonte"), h("dd", {}, TXJ.fonte, ". ", safe ? h("a", { href: safe, target: "_blank", rel: "noopener" }, "Página de dados ↗") : "", ` Última atualização: ${FMT.dateTime(TXJ.atualizado)}; dados até ${txYm(p.mes_fim)}.`),
      h("dt", {}, "Arquivos"), h("dd", {}, (TXJ.arquivos || []).map(a => `${a.ano}: ${a.linhas_total ? FMT.int(a.linhas_total) + " guias, " : ""}baixado em ${FMT.dateTime(a.baixado_em)}, sha256 ${String(a.sha256).slice(0, 12)}…`).join(" · ")),
      h("dt", {}, "Filtros aplicados"), h("dd", {}, TXJ.filtros.join(" · ") + `. Outliers: R$/m² fora de P${p.outliers_percentis[0]}–P${p.outliers_percentis[1]} do bairro na janela (${FMT.int(q.outliers)} transações) e compras em bloco — ${p.lote_minimo}+ unidades do mesmo prédio na mesma data, típico de fundo ou incorporadora (${FMT.int(q.lotes || 0)}): excluídas das métricas; aparecem na tabela com “Incluir outliers e compras em bloco”. Duplicatas removidas: ${q.duplicatas}.`),
      h("dt", {}, "Bairro"), h("dd", {}, `Bairro dos Correios pelo CEP (cobertura ${FMT.share(q.cobertura_cep)}); a coluna "Bairro" do ITBI é texto livre e fica vazia em ~40% das guias. Bairros: ${TXJ.bairros.join(", ")} (Itaim Bibi inclui Chácara Itaim).`),
      h("dt", {}, "Data"), h("dd", {}, "Data da transação declarada na guia (não o mês de pagamento da aba). 99% das guias são pagas até o mês seguinte; o último mês publicado chega incompleto."),
      h("dt", {}, "Área"), h("dd", {}, `A área do ITBI é a área construída do IPTU, que inclui fração das áreas comuns (e vagas vinculadas). Anúncios usam área útil. Área útil equivalente = construída × ${String(p.fator_area_util).replace(".", ",")} — fator calibrado casando 356 prédios presentes nas duas bases (mediana; metade central entre 0,59 e 0,85). Por prédio o fator varia: para um imóvel específico, compare valores totais de unidades parecidas.`),
      h("dt", {}, "Valor"), h("dd", {}, "Valor de transação declarado pelo contribuinte. O imposto incide sobre o maior entre o declarado e o valor venal de referência; o declarado pode não refletir o preço negociado. Sinalizados: declarado abaixo do venal (“< venal”), igual ao venal (“= venal”) e prédio novo (“novo”, preço possivelmente contratado na planta)."),
      h("dt", {}, "Desconto implícito"), h("dd", {}, "Mediana de R$/m² de fechamento ÷ mediana de R$/m² pedido − 1. No bairro inteiro mistura imóveis diferentes (os anúncios do radar têm 100 m² ou mais e a carteira à venda é mais nova e cara que a negociada); use a faixa de área ou o prédio para comparar imóveis parecidos."),
      h("dt", {}, "Não inclui"), h("dd", {}, "Imóveis rurais, ITBI pago via PPI, transações sem compra e venda (doação, partilha, dação, leilão) e frações (< 100% transmitido).")));
}

// transacoes.js carrega com defer (depois do script principal): se a aba já estiver aberta, desenha agora.
if (typeof TAB !== "undefined" && TAB === "transacoes" && DATA) renderTransacoes();
