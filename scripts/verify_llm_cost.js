/**
 * LLM 成本模型自洽性验证与重算（Node.js）。
 * 基准来自 DATA_AND_COST_ANALYSIS.md §4.1.2，并做如下还原：
 *   异动触发的 25,000 in / 6,000 out 是「LIGHT + STANDARD 合计」，需按分档汇总拆分。
 * 所有优化项的效果在此脚本中显式建模，避免文档里出现手算漂移。
 */

const FX = 7.1; // ¥ / $
const usd = (cny) => cny / FX;

// (输入, 输出, 缓存输入) USD / 1M tokens
const PRICES = {
  'qwen3.7-flash': [usd(0.2), usd(0.8), usd(0.2)],  // 上下文缓存折扣未公开，保守按同价计
  'glm-5.3-flash': [usd(0.8), usd(2.8), usd(0.23)],
  'deepseek-v4-flash': [usd(1.0), usd(2.0), usd(0.02)],
  'deepseek-v4-pro': [usd(3.0), usd(6.0), usd(0.025)],
  'glm-5.3': [usd(8.0), usd(28.0), usd(2.0)],
  'qwen3.8-max': [usd(12.0), usd(36.0), usd(3.0)],
  'kimi-k3': [usd(20.0), usd(100.0), usd(2.0)],
  'gpt-6-luna': [0.10, 0.50, 0.01],
  'claude-haiku-5-5': [0.10, 0.50, 0.01],
  'gpt-6.1-terra': [1.00, 5.00, 0.05],
  'gpt-6.1-sol': [2.00, 10.00, 0.10],
  'claude-sonnet-5-5': [2.00, 10.00, 0.10],
  'gpt-6-astra': [10.00, 50.00, 1.00],
  'claude-opus-5-5': [4.00, 20.00, 0.20],
  'claude-fable-5.2': [8.00, 40.00, 0.20],
  'gemini-3.1-pro': [2.00, 12.00, 0.60],
  'grok-4.7': [2.00, 6.00, 0.50],
};

// 基准还原：单资产/天。[次数, 输入/次, 输出/次]
const BASE = {
  L_15m: [96, 7000, 1800],
  L_1h: [24, 18000, 5400],       // 6 个 LIGHT 子 Agent 合计
  L_4h: [6, 8000, 2400],
  L_anom: [17.5, 7271, 694],     // 由分档汇总反解
  S_1h_master: [24, 15000, 2500],
  S_4h: [6, 19000, 3900],
  S_anom: [17.5, 17729, 5306],   // 由分档汇总反解
  D_daily: [1, 24300, 4850],     // 共享 -> 2 资产
};
const LIGHT_KEYS = ['L_15m', 'L_1h', 'L_4h', 'L_anom'];
const STD_KEYS = ['S_1h_master', 'S_4h', 'S_anom'];
const DEEP_KEYS = ['D_daily'];

function sumTier(model, keys, mult = 1) {
  let n = 0, i = 0, o = 0;
  for (const k of keys) { n += model[k][0]; i += model[k][0] * model[k][1]; o += model[k][0] * model[k][2]; }
  return [n, i * mult, o * mult];
}

const f = (v, d = 2) => v.toFixed(d);
const m = (v) => (v / 1e6).toFixed(3) + ' M';
const pc = (v) => (v >= 0 ? '+' : '') + f(v, 1) + '%';

console.log('='.repeat(76));
console.log('A. 基准自洽性检查（单资产/天，对齐 DATA_AND_COST_ANALYSIS §4.1.2）');
console.log('='.repeat(76));
const doc = { LIGHT: [1279250, 328950], STANDARD: [784250, 176250], DEEP: [24300, 4850] };
const [, li, lo] = sumTier(BASE, LIGHT_KEYS);
const [, si, so] = sumTier(BASE, STD_KEYS);
const [, di, do_] = sumTier(BASE, DEEP_KEYS);
for (const [tier, [i, o]] of Object.entries({
  LIGHT: [li, lo], STANDARD: [si, so], DEEP: [di, do_],
})) {
  const [dI, dO] = doc[tier];
  console.log(
    `${tier.padEnd(9)} 还原 ${i.toFixed(0).padStart(11)} / ${o.toFixed(0).padStart(9)}   ` +
    `文档 ${dI.toLocaleString().padStart(11)} / ${dO.toLocaleString().padStart(9)}   ` +
    `Δin ${(i - dI >= 0 ? '+' : '') + (i - dI).toFixed(0)}  Δout ${(o - dO >= 0 ? '+' : '') + (o - dO).toFixed(0)}`
  );
}
const dayIn = 2 * li + 2 * si + 2 * di;
const dayOut = 2 * lo + 2 * so + 2 * do_;
console.log(`\n全量/天  输入 ${dayIn.toLocaleString().padStart(12)}  (文档 4,180,000  Δ${(dayIn - 4180000 >= 0 ? '+' : '') + (dayIn - 4180000).toFixed(0)})`);
console.log(`全量/天  输出 ${dayOut.toLocaleString().padStart(12)}  (文档   820,000  Δ${(dayOut - 820000 >= 0 ? '+' : '') + (dayOut - 820000).toFixed(0)})  <-- E1: 少算 ${f((1 - dayOut / 820000) * 100, 1)}%`);
const anomIn = BASE.L_anom[0] * BASE.L_anom[1] + BASE.S_anom[0] * BASE.S_anom[1];
const anomOut = BASE.L_anom[0] * BASE.L_anom[2] + BASE.S_anom[0] * BASE.S_anom[2];
console.log(`异动还原合计/次  ${anomIn.toFixed(0)} in / ${anomOut.toFixed(0)} out   (文档 §4.1.2 表: 25,000 / 6,000)`);

console.log('\n' + '='.repeat(76));
console.log('B. 月度基准（双资产 x 30 天）');
console.log('='.repeat(76));
const MONTH = {
  LIGHT: [2 * li * 30, 2 * lo * 30],
  STANDARD: [2 * si * 30, 2 * so * 30],
  DEEP: [2 * di * 30, 2 * do_ * 30],
};
for (const [k, [i, o]] of Object.entries(MONTH)) console.log(`${k.padEnd(9)} in ${m(i).padStart(12)}   out ${m(o).padStart(12)}`);
const TOT_IN = Object.values(MONTH).reduce((a, v) => a + v[0], 0);
const TOT_OUT = Object.values(MONTH).reduce((a, v) => a + v[1], 0);
console.log(`${'合计'.padEnd(9)} in ${m(TOT_IN).padStart(12)}   out ${m(TOT_OUT).padStart(12)}`);

console.log('\n--- STANDARD 档内部拆解（单资产/天）---');
const sa = BASE.S_anom[0] * BASE.S_anom[2], s1 = BASE.S_1h_master[0] * BASE.S_1h_master[2], s4 = BASE.S_4h[0] * BASE.S_4h[2];
console.log(`异动触发   out ${sa.toFixed(0).padStart(9)}  ${f(sa / so * 100, 1).padStart(5)}%`);
console.log(`1h Master  out ${s1.toFixed(0).padStart(9)}  ${f(s1 / so * 100, 1).padStart(5)}%`);
console.log(`4h         out ${s4.toFixed(0).padStart(9)}  ${f(s4 / so * 100, 1).padStart(5)}%`);
console.log(`异动触发   in  ${(BASE.S_anom[0] * BASE.S_anom[1]).toFixed(0).padStart(9)}  ${f(BASE.S_anom[0] * BASE.S_anom[1] / si * 100, 1).padStart(5)}%`);

// ---------- 成本函数 ----------
const CACHE_PREFIX = 0.40, CACHE_HIT = 0.60;
function cost(name, tin, tout, hit = CACHE_HIT, prefix = CACHE_PREFIX) {
  const [pi, po, pcv] = PRICES[name];
  const cached = tin * prefix * hit, plain = tin - cached;
  return (plain * pi + cached * pcv) / 1e6 + (tout * po) / 1e6;
}
function costNoCache(name, tin, tout) {
  const [pi, po] = PRICES[name];
  return (tin * pi + tout * po) / 1e6;
}

console.log('\n' + '='.repeat(76));
console.log('C. 优化前：档位成本矩阵');
console.log('='.repeat(76));
const TIER_MODELS = {
  LIGHT: ['qwen3.7-flash', 'gpt-6-luna', 'claude-haiku-5-5', 'glm-5.3-flash', 'deepseek-v4-flash'],
  STANDARD: ['deepseek-v4-pro', 'glm-5.3', 'gpt-6.1-terra', 'qwen3.8-max', 'grok-4.7', 'gemini-3.1-pro', 'gpt-6.1-sol', 'claude-sonnet-5-5', 'kimi-k3', 'claude-opus-5-5'],
  DEEP: ['gpt-6.1-terra', 'deepseek-v4-pro', 'glm-5.3', 'gpt-6.1-sol', 'qwen3.8-max', 'claude-opus-5-5', 'gpt-6-astra', 'claude-fable-5.2'],
};
for (const [tier, models] of Object.entries(TIER_MODELS)) {
  const [tin, tout] = MONTH[tier];
  console.log(`\n[${tier}]  in ${m(tin)} / out ${m(tout)}`);
  for (const mo of models) console.log(`   ${mo.padEnd(22)} $${f(costNoCache(mo, tin, tout))}`);
}

// ---------- 优化模型 ----------
console.log('\n' + '='.repeat(76));
console.log('D. 优化后模型（逐项显式建模）');
console.log('='.repeat(76));
const OPT = {
  L_15m: [8, 5000, 1300],          // O5 降频 + O6 裁剪 + O3 契约
  L_1h: [24, 15000, 3600],         // O6 独立视图 6 x 2500
  L_4h: [6, 7000, 1800],
  L_anom: [3.5, 6000, 500],        // O1 仅 CRITICAL
  S_1h_master: [24, 6500, 1300],   // O2 输入瘦身 + O3 输出契约
  S_4h: [6, 15500, 2600],          // 9000 常规 + 6500 Master
  S_anom: [3.5, 14000, 3500],      // O1 仅 CRITICAL
  D_daily: [1, 24300, 4850],       // DEEP 不变（日报/周报不受前端优化影响）
};
const WARN_SNAPSHOT_PER_MASTER = 600;
const MASTER_CALLS_PER_DAY = 24 + 6;
const [, oli, olo] = sumTier(OPT, LIGHT_KEYS);
const [, osi, oso] = sumTier(OPT, STD_KEYS);
const [, odi, odo] = sumTier(OPT, DEEP_KEYS);
const oSi = osi + MASTER_CALLS_PER_DAY * WARN_SNAPSHOT_PER_MASTER;
const OPT_MONTH = {
  LIGHT: [2 * oli * 30, 2 * olo * 30],
  STANDARD: [2 * oSi * 30, 2 * oso * 30],
  DEEP: [2 * odi * 30, 2 * odo * 30],
};
console.log('档位      优化前 in/out              优化后 in/out              变化');
for (const k of Object.keys(MONTH)) {
  const [bi, bo] = MONTH[k], [oi, oo] = OPT_MONTH[k];
  console.log(`${k.padEnd(9)} ${m(bi).padStart(10)}/${m(bo).padStart(11)}    ${m(oi).padStart(10)}/${m(oo).padStart(11)}    in ${pc((oi / bi - 1) * 100)}  out ${pc((oo / bo - 1) * 100)}`);
}
const OPT_IN = Object.values(OPT_MONTH).reduce((a, v) => a + v[0], 0);
const OPT_OUT = Object.values(OPT_MONTH).reduce((a, v) => a + v[1], 0);
console.log(`合计 token 降幅: in ${pc((OPT_IN / TOT_IN - 1) * 100)}  out ${pc((OPT_OUT / TOT_OUT - 1) * 100)}`);

console.log('\n' + '='.repeat(76));
console.log('E. 方案成本对比（30 天）');
console.log('='.repeat(76));
function planBase(name, light, std, deep) {
  const [li_, lo_] = MONTH.LIGHT, [si_, so_] = MONTH.STANDARD, [di_, do2_] = MONTH.DEEP;
  const c = costNoCache(light, li_, lo_) + costNoCache(std, si_, so_) + costNoCache(deep, di_, do2_);
  console.log(`${name.padEnd(34)} ${'[优化前基线]'.padEnd(22)} ${'$' + f(c).padStart(9)}`);
  return c;
}
function plan(name, light, std, deep, hit = CACHE_HIT, note = '') {
  const [li_, lo_] = OPT_MONTH.LIGHT, [si_, so_] = OPT_MONTH.STANDARD, [di_, do2_] = OPT_MONTH.DEEP;
  const c = cost(light, li_, lo_, hit) + cost(std, si_, so_, hit) + cost(deep, di_, do2_, hit);
  const nc = costNoCache(light, li_, lo_) + costNoCache(std, si_, so_) + costNoCache(deep, di_, do2_);
  console.log(`${name.padEnd(34)} ${note.padEnd(22)} 无缓存 $${f(nc).padStart(7)}   缓存${(hit * 100).toFixed(0)}% $${f(c).padStart(7)}`);
  return c;
}
console.log('\n--- 优化前 ---');
const baseB = planBase("B' haiku + ds-pro + opus", 'claude-haiku-5-5', 'deepseek-v4-pro', 'claude-opus-5-5');
planBase("B'' ds-flash + ds-pro + opus", 'deepseek-v4-flash', 'deepseek-v4-pro', 'claude-opus-5-5');
const baseC = planBase("C' haiku + terra + opus", 'claude-haiku-5-5', 'gpt-6.1-terra', 'claude-opus-5-5');

console.log('\n--- 优化后 ---');
const r1 = plan('R1 ds-flash + ds-pro + opus', 'deepseek-v4-flash', 'deepseek-v4-pro', 'claude-opus-5-5');
plan('R1 (缓存70%)', 'deepseek-v4-flash', 'deepseek-v4-pro', 'claude-opus-5-5', 0.70);
const r2 = plan('R2 ds-flash + terra + opus', 'deepseek-v4-flash', 'gpt-6.1-terra', 'claude-opus-5-5');
const r2_70 = plan('R2 (缓存70%)', 'deepseek-v4-flash', 'gpt-6.1-terra', 'claude-opus-5-5', 0.70);
plan('R3 qwen-flash + ds-pro + ds-pro', 'qwen3.7-flash', 'deepseek-v4-pro', 'deepseek-v4-pro');

console.log(`\nR1 优化后 $${f(r1)} vs 优化前 B' $${f(baseB)}  -> ${pc((r1 / baseB - 1) * 100)}`);
console.log(`R2 优化后 $${f(r2)} vs 优化前 C' $${f(baseC)} -> ${pc((r2 / baseC - 1) * 100)}`);
console.log(`R2(70%) $${f(r2_70)} vs 优化前 B' $${f(baseB)} -> ${pc((r2_70 / baseB - 1) * 100)}`);

console.log('\n' + '='.repeat(76));
console.log('F. 敏感度（基于 R2，缓存 60%）');
console.log('='.repeat(76));
const [Li, Lo] = OPT_MONTH.LIGHT, [Si, So] = OPT_MONTH.STANDARD, [Di, Do] = OPT_MONTH.DEEP;
function r2c(o = {}) {
  const hit = o.hit ?? 0.60, sm = o.sm ?? 1, lm = o.lm ?? 'deepseek-v4-flash', dm = o.dm ?? 'claude-opus-5-5';
  const st = o.st ?? 'gpt-6.1-terra';
  return cost(lm, Li, Lo, hit) + cost(st, Si * sm, So * sm, hit) + cost(dm, Di, Do, hit);
}
console.log(`R2 基准                        $${f(r2c()).padStart(7)}`);
console.log(`缓存命中仅 30%                 $${f(r2c({ hit: 0.30 })).padStart(7)}`);
console.log(`异动频次 x3 (STANDARD x1.4)    $${f(r2c({ sm: 1.40 })).padStart(7)}`);
const save = { ...PRICES };
PRICES['deepseek-v4-flash'] = [usd(2.0), usd(4.0), usd(0.04)];
PRICES['deepseek-v4-pro'] = [usd(6.0), usd(12.0), usd(0.05)];
console.log(`DeepSeek 高峰价                $${f(r2c()).padStart(7)}`);
Object.assign(PRICES, save);

console.log('\n' + '='.repeat(76));
console.log('G. O1 异动分级参与 —— 单独效果（单资产/天）');
console.log('='.repeat(76));
const perIn = BASE.L_anom[1] + BASE.S_anom[1], perOut = BASE.L_anom[2] + BASE.S_anom[2];
console.log(`现状: 17.5 次 x ${perIn.toFixed(0)} in / ${perOut.toFixed(0)} out = ${(17.5 * perIn).toFixed(0)} in / ${(17.5 * perOut).toFixed(0)} out`);
console.log(`修正: CRITICAL 3.5 次独立 = ${(3.5 * perIn).toFixed(0)} in / ${(3.5 * perOut).toFixed(0)} out`);
console.log(`      WARN 10 次并入快照 / INFO 4 次进日报 -> 零额外调用`);
const sAnomInBefore = BASE.S_anom[0] * BASE.S_anom[1];
const sAnomInAfter = 3.5 * OPT.S_anom[1];
console.log(`      STANDARD in: ${sAnomInBefore.toFixed(0)} -> ${sAnomInAfter.toFixed(0)}  降幅 ${f((1 - sAnomInAfter / sAnomInBefore) * 100, 1)}%`);
const sAnomOutBefore = BASE.S_anom[0] * BASE.S_anom[2];
const sAnomOutAfter = 3.5 * OPT.S_anom[2];
console.log(`      STANDARD out: ${sAnomOutBefore.toFixed(0)} -> ${sAnomOutAfter.toFixed(0)}  降幅 ${f((1 - sAnomOutAfter / sAnomOutBefore) * 100, 1)}%`);

console.log('\n' + '='.repeat(76));
console.log('H. 价格口径敏感度（DeepSeek 官方页 vs 第三方聚合口径）');
console.log('='.repeat(76));
console.log('官方页: V4-Flash ¥1/¥2, V4-Pro ¥3/¥6');
console.log('第三方: V4-Flash 闲时 ¥1/¥4, V4-Pro 闲时 ¥4.5/¥13.5 (高峰 x2)');
const save2 = { ...PRICES };
PRICES['deepseek-v4-flash'] = [usd(1.0), usd(4.0), usd(0.04)];
PRICES['deepseek-v4-pro'] = [usd(4.5), usd(13.5), usd(0.15)];
console.log(`R1 第三方闲时口径  $${f(r2c({ st: 'deepseek-v4-pro' })).padStart(7)}`);
PRICES['deepseek-v4-flash'] = [usd(2.0), usd(8.0), usd(0.08)];
PRICES['deepseek-v4-pro'] = [usd(9.0), usd(27.0), usd(0.30)];
console.log(`R1 第三方高峰口径  $${f(r2c({ st: 'deepseek-v4-pro' })).padStart(7)}`);
Object.assign(PRICES, save2);

console.log('\n' + '='.repeat(76));
console.log('I. O1 单项独立贡献（仅做 O1，不做其他优化）');
console.log('='.repeat(76));
const o1Only = { ...BASE };
o1Only.S_anom = [3.5, BASE.S_anom[1], BASE.S_anom[2]];
o1Only.L_anom = [3.5, BASE.L_anom[1], BASE.L_anom[2]];
const [, o1si, o1so] = sumTier(o1Only, STD_KEYS);
const [, o1li, o1lo] = sumTier(o1Only, LIGHT_KEYS);
const o1Std = costNoCache('deepseek-v4-pro', 2 * o1si * 30, 2 * o1so * 30);
const o1Lgt = costNoCache('deepseek-v4-flash', 2 * o1li * 30, 2 * o1lo * 30);
const baseStd = costNoCache('deepseek-v4-pro', MONTH.STANDARD[0], MONTH.STANDARD[1]);
const baseLgt = costNoCache('deepseek-v4-flash', MONTH.LIGHT[0], MONTH.LIGHT[1]);
console.log(`STANDARD(ds-pro): $${f(baseStd)} -> $${f(o1Std)}   节省 $${f(baseStd - o1Std)}`);
console.log(`LIGHT(ds-flash):  $${f(baseLgt)} -> $${f(o1Lgt)}   节省 $${f(baseLgt - o1Lgt)}`);
console.log(`O1 合计节省 $${f(baseStd - o1Std + baseLgt - o1Lgt)}`);

console.log('\n' + '='.repeat(76));
console.log('J. 优化后分档成本明细（用于文档 §5.3 表格核对）');
console.log('='.repeat(76));
for (const [tier, models] of Object.entries(TIER_MODELS)) {
  const [tin, tout] = OPT_MONTH[tier];
  console.log(`\n[${tier}] 优化后 in ${m(tin)} / out ${m(tout)}`);
  for (const mo of models) {
    console.log(`   ${mo.padEnd(22)} 无缓存 $${f(costNoCache(mo, tin, tout)).padStart(8)}` +
      `   缓存60% $${f(cost(mo, tin, tout, 0.60)).padStart(8)}   缓存70% $${f(cost(mo, tin, tout, 0.70)).padStart(8)}`);
  }
}
console.log('\n--- 优化后 R1/R2/R3 组合（分档求和，含缓存 60%）---');
for (const [nm, l, s, d] of [
  ['R1 ds-flash + ds-pro + opus', 'deepseek-v4-flash', 'deepseek-v4-pro', 'claude-opus-5-5'],
  ['R2 ds-flash + terra + opus', 'deepseek-v4-flash', 'gpt-6.1-terra', 'claude-opus-5-5'],
  ['R3 qwen-flash + ds-pro + ds-pro', 'qwen3.7-flash', 'deepseek-v4-pro', 'deepseek-v4-pro'],
]) {
  const c60 = cost(l, OPT_MONTH.LIGHT[0], OPT_MONTH.LIGHT[1], 0.60) +
    cost(s, OPT_MONTH.STANDARD[0], OPT_MONTH.STANDARD[1], 0.60) +
    cost(d, OPT_MONTH.DEEP[0], OPT_MONTH.DEEP[1], 0.60);
  const c70 = cost(l, OPT_MONTH.LIGHT[0], OPT_MONTH.LIGHT[1], 0.70) +
    cost(s, OPT_MONTH.STANDARD[0], OPT_MONTH.STANDARD[1], 0.70) +
    cost(d, OPT_MONTH.DEEP[0], OPT_MONTH.DEEP[1], 0.70);
  console.log(`${nm.padEnd(32)} 缓存60% $${f(c60).padStart(7)}   缓存70% $${f(c70).padStart(7)}`);
}

console.log('\n' + '='.repeat(76));
console.log('K. 每项优化的独立贡献（基于 B2 基线: ds-flash + ds-pro + opus, 30 天）');
console.log('='.repeat(76));

// 每项优化 = 对 BASE 的一个变换；单独施加，度量成本差
const TRANSFORMS = {
  'O1 异动分级（频率 17.5->3.5）': (m) => {
    m.L_anom = [3.5, m.L_anom[1], m.L_anom[2]];
    m.S_anom = [3.5, m.S_anom[1], m.S_anom[2]];
  },
  'O5 analysis_15m 降频（96->8）': (m) => { m.L_15m = [8, m.L_15m[1], m.L_15m[2]]; },
  'O2 Master 输入瘦身': (m) => {
    m.S_1h_master = [24, 6500, m.S_1h_master[2]];
    m.S_4h = [6, 14500, m.S_4h[2]];
  },
  'O3 输出契约收紧': (m) => {
    m.L_15m = [m.L_15m[0], m.L_15m[1], 1300];
    m.L_1h = [m.L_1h[0], m.L_1h[1], 3600];
    m.L_4h = [m.L_4h[0], m.L_4h[1], 1800];
    m.L_anom = [m.L_anom[0], m.L_anom[1], 500];
    m.S_1h_master = [m.S_1h_master[0], m.S_1h_master[1], 1300];
    m.S_4h = [m.S_4h[0], m.S_4h[1], 2600];
    m.S_anom = [m.S_anom[0], m.S_anom[1], 3500];
  },
  'O6 快照视图裁剪': (m) => {
    m.L_1h = [m.L_1h[0], 15000, m.L_1h[2]];
    m.L_4h = [m.L_4h[0], 7000, m.L_4h[2]];
    m.L_anom = [m.L_anom[0], 6000, m.L_anom[2]];
    m.S_anom = [m.S_anom[0], 14000, m.S_anom[2]];
  },
};

function totalCost(model, light, std, deep, hit = null) {
  const [, li, lo] = sumTier(model, LIGHT_KEYS);
  const [, si, so] = sumTier(model, STD_KEYS);
  const [, di, do2_] = sumTier(model, DEEP_KEYS);
  const mi = [2 * li * 30, 2 * lo * 30], msv = [2 * si * 30, 2 * so * 30], md = [2 * di * 30, 2 * do2_ * 30];
  const c = hit === null
    ? costNoCache(light, mi[0], mi[1]) + costNoCache(std, msv[0], msv[1]) + costNoCache(deep, md[0], md[1])
    : cost(light, mi[0], mi[1], hit) + cost(std, msv[0], msv[1], hit) + cost(deep, md[0], md[1], hit);
  return c;
}

const L_M = 'deepseek-v4-flash', S_M = 'deepseek-v4-pro', D_M = 'claude-opus-5-5';
const baseTotal = totalCost(BASE, L_M, S_M, D_M);
console.log(`基线 (B'', 无缓存) $${f(baseTotal)}\n`);
let sumSaving = 0;
for (const [nm, fn] of Object.entries(TRANSFORMS)) {
  const m = { ...BASE };
  fn(m);
  const c = totalCost(m, L_M, S_M, D_M);
  sumSaving += baseTotal - c;
  console.log(`${nm.padEnd(32)} $${f(c).padStart(7)}   节省 $${f(baseTotal - c).padStart(6)}`);
}
// O4 缓存（作用于基线全量）
const cCached = totalCost(BASE, L_M, S_M, D_M, 0.60);
console.log(`${'O4 缓存前缀(命中60%)'.padEnd(32)} $${f(cCached).padStart(7)}   节省 $${f(baseTotal - cCached).padStart(6)}`);
sumSaving += baseTotal - cCached;
// O9 Batch 对 DEEP 档五折的额外节省
const deepBatchSaving = MONTH.DEEP[0] * PRICES[D_M][0] / 1e6 * 0.5 + MONTH.DEEP[1] * PRICES[D_M][1] / 1e6 * 0.5;
console.log(`${'O9 Batch(DEEP档五折)'.padEnd(32)} ${''.padStart(7)}   节省 $${f(deepBatchSaving).padStart(6)}`);
sumSaving += deepBatchSaving;
// 全部叠加
const all = { ...BASE };
for (const fn of Object.values(TRANSFORMS)) fn(all);
const allCached = totalCost(all, L_M, S_M, D_M, 0.60);
console.log(`\n全部叠加(含缓存60%) $${f(allCached)}   相对基线节省 $${f(baseTotal - allCached)}`);
console.log(`单项之和 $${f(sumSaving)}  ->  叠加后 $${f(baseTotal - allCached)}  (非线性放大 ${f(((baseTotal - allCached) / sumSaving - 1) * 100, 1)}%)`);

console.log('\n=== 完成 ===');
