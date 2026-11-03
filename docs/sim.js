/* Simulador comprar × alugar (motor de cálculo, sem interface).
 *
 * Compara o patrimônio ao fim do horizonte em dois cenários com O MESMO desembolso mensal:
 *  - Comprar: paga entrada + custos de aquisição no início e as parcelas do financiamento;
 *    o imóvel valoriza mês a mês. Se o aluguel for maior que a parcela (+ manutenção), a
 *    diferença é investida no Tesouro Selic.
 *  - Alugar: o mesmo caixa inicial (entrada + custos) fica no Tesouro Selic; paga aluguel
 *    (reajustado a cada 12 meses) e investe a diferença quando a parcela seria maior.
 * Condomínio e IPTU não entram: são pagos nos dois cenários.
 *
 * Taxas: Selic do Focus é a taxa de FIM de cada ano; entre dois fins de ano a taxa é
 * interpolada linearmente mês a mês. IGP-M do Focus é a variação de cada ano civil.
 * Anos além do último projetado repetem o último valor. Tesouro Selic rende % da Selic,
 * menos taxa de custódia (ao ano, sobre o saldo); IR regressivo aplicado no resgate final
 * sobre o ganho total, pela duração do horizonte (aproximação: aportes posteriores ficam
 * menos tempo aplicados).
 */
(function (root) {
  "use strict";
  const mrate = a => Math.pow(1 + a, 1 / 12) - 1;

  function pathValue(path, year) {
    const ys = Object.keys(path || {}).map(Number).sort((a, b) => a - b);
    if (!ys.length) return null;
    if (year <= ys[0]) return path[ys[0]] / 100;
    if (year >= ys[ys.length - 1]) return path[ys[ys.length - 1]] / 100;
    return path[year] != null ? path[year] / 100 : path[ys.filter(y => y < year).pop()] / 100;
  }
  // Selic anual vigente no mês m (1..12) do ano y: interpola do fim do ano anterior ao fim do ano y.
  function selicAt(path, y, m) {
    const end = pathValue(path, y), prev = pathValue(path, y - 1);
    if (end == null) return null;
    const start = Object.keys(path).map(Number).some(k => k <= y - 1) ? prev : end;
    return start + (end - start) * (m / 12);
  }
  function irRate(months) {
    const d = months * 30.4;
    return d <= 180 ? .225 : d <= 360 ? .20 : d <= 720 ? .175 : .15;
  }
  const afterTax = (value, contrib, months) => value - Math.max(0, value - contrib) * irRate(months);

  /** p: parâmetros (percentuais em fração: 0.3 = 30%). Ver defaults em DEFAULTS. */
  function simulate(p) {
    const months = Math.round(p.horizonYears * 12);
    const loan0 = p.price * (1 - p.downPct), n = Math.round(p.termYears * 12), j = mrate(p.rateAA);
    const pmt = loan0 > 0 ? (j > 0 ? loan0 * j / (1 - Math.pow(1 + j, -n)) : loan0 / n) : 0;
    const cash0 = p.price * p.downPct + p.price * p.acqPct;
    let V = p.price, L = loan0, R = p.rent, rentAcc = 1, defl = 1;
    let portR = cash0, contR = cash0, portB = 0, contB = 0, firstInst = null, sumInst = 0, sumRent = 0, sumInt = 0;
    const snap = t => {
      const buy = V * (1 - p.sellCostPct) - L + afterTax(portB, contB, t), rent = afterTax(portR, contR, t);
      return { t, year: t / 12, buy, rent, diff: buy - rent, buyReal: buy / defl, rentReal: rent / defl, V, L, portB, portR, R, defl };
    };
    const rows = [snap(0)];
    for (let t = 1; t <= months; t++) {
      const idx = p.startMonth - 1 + (t - 1), y = p.startYear + Math.floor(idx / 12), m = (idx % 12) + 1;
      const selic = (selicAt(p.selicPath, y, m) ?? p.selicFallback) + (p.selicShift || 0);
      const igpm = (pathValue(p.igpmPath, y) ?? p.igpmFallback);
      const inv = Math.pow(1 + selic * p.tesouroPct, 1 / 12) * Math.pow(1 - p.custodyAA, 1 / 12) - 1;
      const appr = (p.apprAA != null ? p.apprAA : igpm) + (p.apprShift || 0);
      const rentAdj = p.rentAdjAA != null ? p.rentAdjAA : igpm;
      portR *= 1 + inv; portB *= 1 + inv;
      V *= 1 + mrate(appr);
      defl *= 1 + mrate(igpm);
      let inst = 0;
      if (L > 0.005 && t <= n) {
        const interest = L * j;
        const amort = Math.min(L, p.system === "PRICE" ? pmt - interest : loan0 / n);
        inst = interest + amort; L -= amort; sumInt += interest;
      }
      if (firstInst == null && inst) firstInst = inst;
      sumInst += inst; sumRent += R;
      const buyOut = inst + V * p.maintAA / 12;
      if (buyOut > R) { portR += buyOut - R; contR += buyOut - R; } else { portB += R - buyOut; contB += R - buyOut; }
      rentAcc *= 1 + mrate(rentAdj);
      if (t % 12 === 0) { R *= rentAcc; rentAcc = 1; }
      if (t % 12 === 0 || t === months) rows.push(snap(t));
    }
    const last = rows[rows.length - 1];
    return { rows, final: last, cash0, loan0, firstInst, pmt, sumInst, sumRent, sumInt, months };
  }

  // Valorização anual (constante) do imóvel que empata os dois cenários.
  function breakevenAppr(p) {
    const f = a => simulate({ ...p, apprAA: a, apprShift: 0 }).final.diff;
    let lo = -0.2, hi = 0.5, flo = f(lo), fhi = f(hi);
    if (flo * fhi > 0) return null;
    for (let k = 0; k < 60; k++) { const mid = (lo + hi) / 2, fm = f(mid); if (flo * fm <= 0) { hi = mid; fhi = fm; } else { lo = mid; flo = fm; } }
    return (lo + hi) / 2;
  }

  function sensitivity(p, apprShifts, selicShifts) {
    return apprShifts.map(a => selicShifts.map(s => simulate({ ...p, apprShift: (p.apprShift || 0) + a, selicShift: (p.selicShift || 0) + s }).final.diff));
  }

  const DEFAULTS = { downPct: .3, acqPct: .04, rateAA: .115, termYears: 30, system: "SAC", horizonYears: 10,
    apprAA: null, rentAdjAA: null, tesouroPct: 1, custodyAA: .002, maintAA: 0, sellCostPct: 0,
    selicFallback: .12, igpmFallback: .04 };

  const api = { simulate, breakevenAppr, sensitivity, pathValue, selicAt, irRate, DEFAULTS };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.SIM = api;
})(typeof window !== "undefined" ? window : globalThis);
