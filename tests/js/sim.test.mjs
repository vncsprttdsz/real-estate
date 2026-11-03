// Testes do motor do simulador: node tests/js/sim.test.mjs
import { createRequire } from "module";
const SIM = createRequire(import.meta.url)("../../docs/sim.js");
let fails = 0;
const ok = (cond, msg) => { console.log((cond ? "ok   " : "FALHA") + " " + msg); if (!cond) fails++; };
const near = (a, b, tol) => Math.abs(a - b) <= tol;
const base = { ...SIM.DEFAULTS, price: 1_000_000, rent: 4_000, startYear: 2027, startMonth: 1,
  selicPath: { 2027: 10, 2028: 10, 2029: 10 }, igpmPath: { 2027: 4, 2028: 4 } };

// 1) À vista, sem custos, sem aluguel: imóvel valoriza 10% a.a.; alugar = mesmo caixa no Tesouro a 10% − IR 15%
let r = SIM.simulate({ ...base, downPct: 1, acqPct: 0, rent: 0, custodyAA: 0, apprAA: .10, horizonYears: 5 });
ok(near(r.final.buy, 1e6 * 1.1 ** 5, 1), `à vista: imóvel = 1 mi × 1,1^5 (${Math.round(r.final.buy)})`);
ok(near(r.final.rent, 1e6 + (1e6 * 1.1 ** 5 - 1e6) * .85, 1), `à vista: caixa líquido de IR 15% (${Math.round(r.final.rent)})`);

// 2) SAC: amortização constante, saldo zera no prazo, 1ª parcela = amortização + juros do 1º mês
r = SIM.simulate({ ...base, downPct: .2, acqPct: 0, termYears: 10, horizonYears: 10, rateAA: .12 });
const j = 1.12 ** (1 / 12) - 1;
ok(near(r.final.L, 0, 1), "SAC: saldo devedor zerado ao fim do prazo");
ok(near(r.firstInst, 800_000 / 120 + 800_000 * j, .01), `SAC: 1ª parcela ${r.firstInst.toFixed(2)}`);

// 3) Price: parcela constante pela fórmula
r = SIM.simulate({ ...base, downPct: .2, acqPct: 0, termYears: 10, horizonYears: 2, rateAA: .12, system: "PRICE" });
ok(near(r.pmt, 800_000 * j / (1 - (1 + j) ** -120), .01) && near(r.firstInst, r.pmt, .01), `Price: parcela constante ${r.pmt.toFixed(2)}`);

// 4) Mesmo desembolso: o caixa inicial do inquilino = entrada + custos de aquisição
r = SIM.simulate({ ...base, downPct: .3, acqPct: .04 });
ok(near(r.cash0, 340_000, .01) && near(r.rows[0].rent, 340_000, .01), "caixa inicial = entrada 30% + custos 4%");
ok(near(r.rows[0].buy, 1e6 - 700_000, .01), "patrimônio inicial comprando = imóvel − dívida (custos de aquisição saem do patrimônio)");

// 5) Aluguel reajustado a cada 12 meses pelo IGP-M (4% a.a.)
r = SIM.simulate({ ...base, horizonYears: 2 });
ok(near(r.rows[1].R, 4000 * 1.04, .01) && near(r.rows[2].R, 4000 * 1.04 ** 2, .01), `aluguel reajustado: ${r.rows[1].R.toFixed(2)}, ${r.rows[2].R.toFixed(2)}`);

// 6) Selic interpolada entre fins de ano
const p = { 2027: 12, 2028: 9 };
ok(near(SIM.selicAt(p, 2028, 6), .105, 1e-9) && near(SIM.selicAt(p, 2028, 12), .09, 1e-9) && near(SIM.selicAt(p, 2027, 3), .12, 1e-9), "Selic: interpolação linear de 12% (fim 2027) a 9% (fim 2028)");
ok(SIM.pathValue(p, 2035) === .09, "anos além do Focus repetem o último valor");

// 7) Empate: na valorização de empate a diferença é ~0, e a diferença cresce com a valorização
const be = SIM.breakevenAppr(base);
ok(be != null && near(SIM.simulate({ ...base, apprAA: be }).final.diff, 0, 50), `valorização de empate ${(be * 100).toFixed(2)}% a.a.`);
ok(SIM.simulate({ ...base, apprAA: be + .02 }).final.diff > 0 && SIM.simulate({ ...base, apprAA: be - .02 }).final.diff < 0, "acima do empate compra ganha; abaixo, aluguel ganha");

// 8) Sensibilidade: Selic maior favorece alugar
const g = SIM.sensitivity(base, [0], [-.02, 0, .02])[0];
ok(g[0] > g[1] && g[1] > g[2], "Selic +2 pp reduz a vantagem de comprar");

// 9) FGTS na entrada: reduz o caixa inicial dos dois cenários; o saldo restante segue no FGTS
const fb = { ...base, downPct: .3, acqPct: .04, fgtsBalance: 200_000, fgtsMode: "nenhum", fgtsYieldAA: .05, ipcaPath: { 2027: 4 } };
r = SIM.simulate(fb);
ok(near(r.cash0, 140_000, .01) && near(r.fgts.down, 200_000, .01), "FGTS: entrada 300 mil − 200 mil de FGTS + custos 40 mil = 140 mil de caixa");
ok(near(r.rows[0].rent, 140_000 + 200_000, .01) && near(r.rows[0].buy, 300_000, .01), "FGTS: inquilino mantém o saldo no FGTS; comprador o converte em entrada");
r = SIM.simulate({ ...fb, fgtsBalance: 500_000 });
ok(near(r.fgts.down, 300_000, .01) && near(r.rows[0].fgtsB, 200_000, .01), "FGTS: usa no máximo o valor da entrada; o excesso fica no FGTS");
r = SIM.simulate({ ...fb, sfhCap: 900_000 });
ok(!r.fgts.ok && near(r.cash0, 340_000, .01), "FGTS: imóvel acima do teto do SFH não usa FGTS");
r = SIM.simulate({ ...fb, fgtsUseDown: false, fgtsBalance: 0, fgtsMonthly: 1000, horizonYears: 1, fgtsYieldAA: 0, ipcaPath: { 2027: 0 } });
ok(near(r.final.fgtsR, 12_000, .01), "FGTS: inquilino acumula 12 depósitos de 1.000 (rendimento 0)");

// 10) Parcelas: até 80% de cada parcela, com o saldo do início de cada ciclo de 12 meses
r = SIM.simulate({ ...base, downPct: .2, acqPct: 0, termYears: 10, horizonYears: 3, rateAA: .12, fgtsBalance: 1e6, fgtsUseDown: false, fgtsMode: "parcelas" });
ok(near(r.firstPaid, r.firstInst * .2, .01), `parcelas: FGTS paga 80% da 1ª parcela (paga em dinheiro ${r.firstPaid.toFixed(2)})`);
r = SIM.simulate({ ...base, downPct: .2, acqPct: 0, termYears: 10, horizonYears: 2, rateAA: .12, fgtsBalance: 0, fgtsMonthly: 2000, fgtsUseDown: false, fgtsMode: "parcelas", fgtsYieldAA: 0, ipcaPath: { 2027: 0 }, fgtsDepAdj: false });
ok(near(r.fgts.usedInst, 24_000, .01) && near(r.final.fgtsB, 24_000, .01), "parcelas: depósitos do ano 1 só pagam parcelas no ciclo seguinte");

// 11) Amortização a cada 2 anos (uso na entrada conta como o primeiro)
const am = { ...base, downPct: .2, acqPct: 0, termYears: 20, horizonYears: 5, rateAA: .12, fgtsBalance: 300_000, fgtsMonthly: 1000, fgtsMode: "amortizacao", fgtsYieldAA: 0, ipcaPath: { 2027: 0 }, fgtsDepAdj: false };
r = SIM.simulate(am);
ok(near(r.fgts.down, 200_000, .01) && near(r.fgts.usedAmort, 100_000 + 23_000 + 24_000, .01), "amortização: meses 24 (100 mil + 23 depósitos) e 48 (24 depósitos)");
const noF = SIM.simulate({ ...am, fgtsMode: "nenhum" });
ok(r.final.L < noF.final.L - 100_000, "amortização: saldo devedor menor que sem uso do FGTS");
console.log(fails ? `\n${fails} falha(s)` : "\ntodos os testes passaram");
process.exit(fails ? 1 : 0);
