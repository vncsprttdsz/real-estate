/* Transações (ITBI) — funções de cálculo, sem interface. Usado pelo painel (window.ITBI) e pelos testes (node).
 *
 * Área: o ITBI traz a área construída do IPTU (inclui fração de áreas comuns). Para comparar com anúncios (área
 * útil), a área útil equivalente = área construída × fator (padrão 0,72, calibrado casando 356 prédios).
 */
(function (root) {
  "use strict";
  const TYPES = new Set(["R", "RUA", "AV", "AVENIDA", "AL", "ALAMEDA", "PC", "PCA", "PRACA", "TV", "TRAVESSA", "LGO", "LARGO", "VL", "VIELA", "PQ", "PARQUE", "EST", "ESTRADA", "PSG", "PASSAGEM"]);
  const ABBR = { DOUTOR: "DR", DOUTORA: "DRA", PROFESSOR: "PROF", PROFESSORA: "PROFA", SENADOR: "SEN", GENERAL: "GEN", PADRE: "PE", SANTO: "STO", SANTA: "STA",
    SAO: "S", CAPITAO: "CAP", CORONEL: "CEL", DEPUTADO: "DEP", ENGENHEIRO: "ENG", BRIGADEIRO: "BRIG", PRESIDENTE: "PRES", MINISTRO: "MIN", GOVERNADOR: "GOV",
    MARECHAL: "MAL", COMENDADOR: "COM", DESEMBARGADOR: "DES", MAESTRO: "MTO", JORNALISTA: "JORN", VEREADOR: "VER", CARDEAL: "CARD", DONA: "D", DOM: "D",
    EMBAIXADOR: "EMB", ALMIRANTE: "ALM", PREFEITO: "PREF", TENENTE: "TEN", ARQUITETO: "ARQ", MONSENHOR: "MONS", CONSELHEIRO: "CONS", MAJOR: "MAJ", JUNIOR: "JR", FILHO: "FO",
    DO: "", DA: "", DE: "", DOS: "", DAS: "", E: "" };
  const plain = t => String(t ?? "").normalize("NFD").replace(/[̀-ͯ]/g, "").toUpperCase().replace(/[^A-Z0-9 ]/g, " ").replace(/\s+/g, " ").trim();

  /** Chave do logradouro sem tipo nem preposições, com títulos abreviados: "Rua Senador Otávio Mangabeira" → "SEN OTAVIO MANGABEIRA". */
  function streetKey(name) {
    let w = plain(name).split(" ").filter(Boolean).map(t => ABBR[t] ?? t);
    if (w.length > 1 && TYPES.has(w[0])) w = w.slice(1);
    return w.filter(Boolean).join(" ");
  }
  const addrKey = (street, number) => streetKey(street) + "|" + (number == null || number === "" ? "" : String(parseInt(number, 10)));

  /** Texto livre ("Rua Joaquim Floriano, 100 ap 51") → { street, number, key }. Sem número: key termina em "|". */
  function parseAddress(text) {
    const t = String(text || "").trim();
    const m = t.match(/^(.*?)[\s,]+(?:n[ºo°.]?\s*)?(\d{1,5})\b(.*)$/i);
    const street = m ? m[1] : t, number = m ? m[2] : "";
    return { street, number, key: addrKey(street, number), skey: streetKey(street) };
  }

  /** dados de transacoes.json → objetos com área útil equivalente e R$/m². */
  function decode(json, fator) {
    const F = Object.fromEntries(json.campos.map((c, i) => [c, i])), f = fator ?? json.parametros.fator_area_util;
    return json.linhas.map(x => {
      const ac = x[F.area_construida], v = x[F.valor], au = ac * f;
      return { d: x[F.data], ym: x[F.data].slice(0, 7), b: json.bairros[x[F.bairro]], log: x[F.logradouro], num: x[F.numero], cpl: x[F.complemento] || "",
        cep: x[F.cep], ac, au, v, vv: x[F.venal_ref], fin: !!x[F.financiado], acc: x[F.acc], pad: x[F.padrao], flags: x[F.flags] || "",
        ppmU: v / au, ppmC: v / ac, k: addrKey(x[F.logradouro], x[F.numero]), sk: streetKey(x[F.logradouro]) };
    });
  }

  function addMonths(ym, k) { let y = +ym.slice(0, 4), m = +ym.slice(5, 7) + k; while (m <= 0) { y--; m += 12; } while (m > 12) { y++; m -= 12; } return `${y}-${String(m).padStart(2, "0")}`; }
  const monthRange = (endYm, months) => ({ from: addMonths(endYm, -(months - 1)), to: endYm });
  const inRange = (r, rg) => r.ym >= rg.from && r.ym <= rg.to;

  function quantile(sorted, q) {
    if (!sorted.length) return null;
    const k = (sorted.length - 1) * q, lo = Math.floor(k), hi = Math.min(lo + 1, sorted.length - 1);
    return sorted[lo] + (sorted[hi] - sorted[lo]) * (k - lo);
  }
  function stats(vals) {
    const a = vals.filter(v => v != null && isFinite(v)).sort((x, y) => x - y);
    return { n: a.length, p25: quantile(a, .25), p50: quantile(a, .5), p75: quantile(a, .75), mean: a.length ? a.reduce((s, v) => s + v, 0) / a.length : null };
  }

  const BANDS = [[0, 100, "até 100 m²"], [100, 150, "100–150 m²"], [150, 200, "150–200 m²"], [200, 300, "200–300 m²"], [300, Infinity, "300 m² ou mais"]];
  const bandOf = area => BANDS.findIndex(([lo, hi]) => area >= lo && area < hi);
  const AGES = [[0, 10, "até 10 anos"], [10, 30, "10–30 anos"], [30, Infinity, "30 anos ou mais"]];
  const ageOf = (acc, year) => acc ? AGES.findIndex(([lo, hi]) => year - acc >= lo && year - acc < hi) : -1;

  /** Transações usadas nas métricas: sem outliers ("o") nem compras em bloco ("l"), salvo pedido. */
  const usable = (rows, withOutliers) => withOutliers ? rows : rows.filter(r => !/[ol]/.test(r.flags));

  /** Série mensal: mediana e P25–P75 de R$/m² (útil equivalente ou construída). */
  function monthly(rows, rg, basis = "U") {
    const out = [];
    for (let ym = rg.from; ym <= rg.to; ym = addMonths(ym, 1)) {
      const s = stats(rows.filter(r => r.ym === ym).map(r => basis === "U" ? r.ppmU : r.ppmC));
      out.push({ ym, ...s });
    }
    return out;
  }

  /** Desconto implícito: mediana de fechamento ÷ mediana pedida − 1. */
  const discount = (closed, asked) => closed && asked ? closed / asked - 1 : null;

  /** Referências para uma oferta: prédio (mesmo endereço) e bairro + faixa de área, no período. */
  function offer({ price, area, key, bairro }, rows, minN = 5) {
    const ask = price && area ? price / area : null;
    const bld = key && !key.endsWith("|") ? stats(rows.filter(r => r.k === key).map(r => r.ppmU)) : { n: 0 };
    const bi = area ? bandOf(area) : -1;
    const nb = bairro ? stats(rows.filter(r => r.b === bairro && (bi < 0 || bandOf(r.au) === bi)).map(r => r.ppmU)) : { n: 0 };
    const nbAll = bairro ? stats(rows.filter(r => r.b === bairro).map(r => r.ppmU)) : { n: 0 };
    const ref = bld.n >= 3 ? { kind: "prédio", ...bld } : nb.n >= minN ? { kind: "bairro e faixa", ...nb } : nbAll.n >= minN ? { kind: "bairro", ...nbAll } : null;
    return { ask, bld, nb, nbAll, band: bi, ref,
      disc: ref && ask ? discount(ref.p50, ask) : null,
      range: ref && area ? [ref.p25 * area, ref.p50 * area, ref.p75 * area] : null };
  }

  const api = { streetKey, addrKey, parseAddress, decode, addMonths, monthRange, inRange, quantile, stats, BANDS, bandOf, AGES, ageOf, usable, monthly, discount, offer, plain };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.ITBI = api;
})(typeof window !== "undefined" ? window : globalThis);
