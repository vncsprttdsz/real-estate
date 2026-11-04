// Testes do motor de transações (docs/itbi.js): node tests/js/itbi.test.mjs
import { createRequire } from "module";
const I = createRequire(import.meta.url)("../../docs/itbi.js");
let fails = 0;
const ok = (c, m) => { console.log((c ? "ok   " : "FALHA") + " " + m); if (!c) fails++; };
const near = (a, b, t) => Math.abs(a - b) <= t;

// Endereços: grafia do ITBI × anúncio × texto livre
ok(I.streetKey("R SEN OTAVIO MANGABEIRA") === I.streetKey("Rua Senador Otávio Mangabeira"), "títulos abreviados (SEN = Senador)");
ok(I.streetKey("R LEOPOLDO COUTO MAGALHAES JR.") === I.streetKey("Rua Leopoldo Couto de Magalhães Júnior"), "Júnior = JR, sem 'de'");
ok(I.streetKey("AV BRIG FARIA LIMA") === I.streetKey("Avenida Brigadeiro Faria Lima"), "AV = Avenida, BRIG = Brigadeiro");
ok(I.streetKey("AL LORENA") === "LORENA" && I.streetKey("Al. Lorena") === "LORENA", "AL/Al. = Alameda");
const a = I.parseAddress("R. Joaquim Floriano nº 100 ap 51");
ok(a.key === "JOAQUIM FLORIANO|100" && a.number === "100", `texto livre com nº e unidade → ${a.key}`);
ok(I.parseAddress("Rua Tabapuã").key === "TABAPUA|", "sem número: só a rua");

// Decodificação e área útil equivalente
const J = { campos: ["data", "bairro", "logradouro", "numero", "complemento", "cep", "area_construida", "valor", "venal_ref", "financiado", "acc", "padrao", "flags"],
  bairros: ["Itaim Bibi", "Jardim Paulista"], parametros: { fator_area_util: .72, mes_fim: "2026-09" },
  linhas: [["2026-09-10", 0, "R JOAQUIM FLORIANO", 100, "AP 51", "04534000", 200, 2_000_000, 0, 0, 1990, "C", ""],
           ["2026-08-10", 0, "R JOAQUIM FLORIANO", 100, "AP 71", "04534000", 200, 2_200_000, 0, 1, 1990, "C", ""],
           ["2026-03-01", 1, "AL LORENA", 1500, "AP 1", "01424000", 100, 300_000, 0, 0, 1980, "B", "o"],
           ["2026-05-28", 1, "AL LORENA", 10, "AP 2", "01424000", 100, 1_500_000, 0, 0, 2022, "E", "l"]] };
const R = I.decode(J);
ok(near(R[0].au, 144, 1e-9) && near(R[0].ppmU, 2e6 / 144, 1e-6) && near(R[0].ppmC, 1e4, 1e-9), "área útil eq. = construída × 0,72; R$/m² nas duas bases");
ok(R[0].k === a.key, "transação casa com o endereço digitado");
ok(I.usable(R).length === 2 && I.usable(R, true).length === 4, "métricas sem outliers nem compras em bloco");

// Janela e estatísticas
const rg = I.monthRange("2026-09", 12);
ok(rg.from === "2025-10" && I.addMonths("2026-01", -1) === "2025-12", "janela de 12 meses até set/26 começa em out/25");
const st = I.stats([1, 2, 3, 4, 5]);
ok(st.n === 5 && st.p50 === 3 && st.p25 === 2 && st.p75 === 4, "quartis");
ok(I.bandOf(99) === 0 && I.bandOf(100) === 1 && I.bandOf(150) === 2 && I.bandOf(450) === 4, "faixas de área");
const ser = I.monthly(I.usable(R), I.monthRange("2026-09", 2), "U");
ok(ser.length === 2 && ser[0].ym === "2026-08" && ser[1].n === 1, "série mensal com meses vazios");

// Oferta: prédio com ≥ 3 transações vira a referência
const rows = [0, 1, 2].map(i => ({ ...R[0], ppmU: 12_000 + i * 1000, au: 140 }));
const o = I.offer({ price: 2_100_000, area: 140, key: a.key, bairro: "Itaim Bibi" }, rows, 5);
ok(o.ref.kind === "prédio" && near(o.ref.p50, 13_000, 1e-9) && near(o.disc, 13_000 / 15_000 - 1, 1e-9), `referência do prédio; desconto ${(o.disc * 100).toFixed(1)}%`);
ok(near(o.range[1], 13_000 * 140, 1e-6), "faixa de valores = R$/m² × área");
const o2 = I.offer({ price: 2_100_000, area: 140, key: "OUTRA|1", bairro: "Itaim Bibi" }, rows, 5);
ok(o2.ref === null, "sem prédio e com amostra pequena no bairro: sem referência");
console.log(fails ? `\n${fails} falha(s)` : "\ntodos os testes passaram");
process.exit(fails ? 1 : 0);
