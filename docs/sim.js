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
 * FGTS (opcional): os dois cenários recebem os mesmos depósitos mensais. Alugando, o saldo
 * fica no FGTS (rende TR + 3% + distribuição de lucros, com piso no IPCA). Comprando um
 * imóvel enquadrado no SFH (preço até o teto), o saldo pode compor a entrada e os depósitos
 * podem (a) pagar até 80% das parcelas em ciclos de 12 meses — o saldo existente no início
 * do ciclo é o disponível para os 12 meses — ou (b) amortizar o saldo devedor a cada 2 anos
 * (o uso na entrada conta como o primeiro), reduzindo a parcela. O saldo de FGTS entra no
 * patrimônio dos dois cenários (sem IR).
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
    // FGTS: só no cenário de compra e só se o imóvel couber no SFH
    const fgts0 = Math.max(0, p.fgtsBalance || 0), fgtsDep0 = Math.max(0, p.fgtsMonthly || 0);
    const fgtsOK = (fgts0 > 0 || fgtsDep0 > 0) && p.price <= (p.sfhCap ?? Infinity) && p.fgtsEligible !== false;
    const fgtsDown = fgtsOK && p.fgtsUseDown ? Math.min(fgts0, p.price * p.downPct) : 0;
    const mode = fgtsOK && loan0 > 0 ? (p.fgtsMode || "nenhum") : "nenhum";
    const cash0 = p.price * p.downPct - fgtsDown + p.price * p.acqPct;
    let V = p.price, L = loan0, R = p.rent, rentAcc = 1, defl = 1;
    let portR = cash0, contR = cash0, portB = 0, contB = 0, firstInst = null, firstPaid = null, sumInst = 0, sumRent = 0, sumInt = 0;
    let FR = fgts0, FB = fgts0 - fgtsDown, dep = fgtsDep0, depAcc = 1, avail = 0, lastUse = fgtsDown > 0 ? 0 : -Infinity;
    let usedInst = 0, usedAmort = 0, curPmt = pmt, sacAmort = n ? loan0 / n : 0;
    const snap = t => {
      const buy = V * (1 - p.sellCostPct) - L + afterTax(portB, contB, t) + FB, rent = afterTax(portR, contR, t) + FR;
      return { t, year: t / 12, buy, rent, diff: buy - rent, buyReal: buy / defl, rentReal: rent / defl, V, L, portB, portR, R, defl, fgtsB: FB, fgtsR: FR };
    };
    const rows = [snap(0)];
    for (let t = 1; t <= months; t++) {
      const idx = p.startMonth - 1 + (t - 1), y = p.startYear + Math.floor(idx / 12), m = (idx % 12) + 1;
      const selic = (selicAt(p.selicPath, y, m) ?? p.selicFallback) + (p.selicShift || 0);
      const igpm = (pathValue(p.igpmPath, y) ?? p.igpmFallback);
      const ipca = (pathValue(p.ipcaPath, y) ?? p.ipcaFallback ?? igpm);
      const fy = Math.max(p.fgtsYieldAA ?? 0.045, ipca);
      const inv = Math.pow(1 + selic * p.tesouroPct, 1 / 12) * Math.pow(1 - p.custodyAA, 1 / 12) - 1;
      const appr = (p.apprAA != null ? p.apprAA : igpm) + (p.apprShift || 0);
      const rentAdj = p.rentAdjAA != null ? p.rentAdjAA : igpm;
      portR *= 1 + inv; portB *= 1 + inv;
      FR *= 1 + mrate(fy); FB *= 1 + mrate(fy);
      V *= 1 + mrate(appr);
      defl *= 1 + mrate(igpm);
      const active = L > 0.005 && t <= n;
      // amortização extraordinária com FGTS (reduz a parcela; prazo mantido)
      if (active && mode === "amortizacao" && t - lastUse >= (p.fgtsIntervalM ?? 24) && FB > 1) {
        const a = Math.min(FB, L); L -= a; FB -= a; usedAmort += a; lastUse = t;
        const left = n - t + 1;
        sacAmort = L / left;
        curPmt = L > 0 ? (j > 0 ? L * j / (1 - Math.pow(1 + j, -left)) : L / left) : 0;
      }
      if (mode === "parcelas" && (t - 1) % 12 === 0) avail = FB;   // novo ciclo de 12 meses
      let inst = 0, fUse = 0;
      if (L > 0.005 && t <= n) {
        const interest = L * j;
        const amort = Math.min(L, p.system === "PRICE" ? curPmt - interest : sacAmort);
        inst = interest + amort; L -= amort; sumInt += interest;
        if (mode === "parcelas") { fUse = Math.min(inst * (p.fgtsInstPct ?? 0.8), avail, FB); avail -= fUse; FB -= fUse; usedInst += fUse; }
      }
      if (firstInst == null && inst) { firstInst = inst; firstPaid = inst - fUse; }
      sumInst += inst; sumRent += R;
      const buyOut = inst - fUse + V * p.maintAA / 12;
      if (buyOut > R) { portR += buyOut - R; contR += buyOut - R; } else { portB += R - buyOut; contB += R - buyOut; }
      FR += dep; FB += dep;                                         // depósito do empregador (8% do salário)
      rentAcc *= 1 + mrate(rentAdj); depAcc *= 1 + mrate(ipca);
      if (t % 12 === 0) { R *= rentAcc; rentAcc = 1; if (p.fgtsDepAdj !== false) dep *= depAcc; depAcc = 1; }
      if (t % 12 === 0 || t === months) rows.push(snap(t));
    }
    const last = rows[rows.length - 1];
    return { rows, final: last, cash0, loan0, firstInst, firstPaid, pmt, sumInst, sumRent, sumInt, months,
      fgts: { ok: fgtsOK, down: fgtsDown, usedInst, usedAmort, mode, finalB: FB, finalR: FR } };
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
    selicFallback: .12, igpmFallback: .04, ipcaFallback: .04,
    fgtsBalance: 0, fgtsMonthly: 0, fgtsUseDown: true, fgtsMode: "parcelas", fgtsYieldAA: .045, fgtsInstPct: .8,
    fgtsIntervalM: 24, fgtsDepAdj: true, sfhCap: 2250000 };

  const api = { simulate, breakevenAppr, sensitivity, pathValue, selicAt, irRate, DEFAULTS };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.SIM = api;
})(typeof window !== "undefined" ? window : globalThis);
