#!/usr/bin/env node
/**
 * bpt_diagram_runtime.js — единственный лейаутер проекции дерева БП
 * в шейпе дизайнера Б24 (волна 7 bpt-diagram; план
 * внутренний план волны 7).
 *
 * ЧИСТАЯ ФУНКЦИЯ БЕЗ DOM: layout(tree, paint, foldState, opts) →
 * {svg, coords, nodeIndex, stats}. Тот же код даёт:
 *   - X.svg          → `node bpt_diagram_runtime.js tree.json [paint.json]
 *                       [fold.json] [--palette=test] > X.svg` (статика);
 *   - X.diagram.html → python pack_html() инлайнит этот файл + JSON
 *                       + бутстрап (свёртка/инспекция).
 *
 * Контракт входа (готовит bpt_diagram.py load_tree):
 *   tree = {
 *     root: {name, type, title, subtitle?, children[]},
 *       // children могут содержать призраки: {ghost:true, name, title,
 *       // count} — интерколяция по порядку A сделана в python; runtime
 *       // только рисует.
 *     templateData?: {parameters:[{name,title,type,required,default?}],
 *                      variables:[...], constants:[...]}   // блок данных
 *   }
 *   paint = {name: 'added'|'modified'|'removed'}
 *   foldState = [name,...] — свёрнутые контейнеры/ветки
 *   opts = {palette?: 'default'|'test'|объект, debug?: bool}
 *
 * Шейп — РЕФЕРЕНС дизайнера («похоже, не 1:1», слово владельца
 * 2026-10-16): хребет вниз; IfElse = бар условия + веер колонок веток
 * (чип-лейбл ветки + серый субтитр условия) + merge-точка; While/ForEach
 * = заголовок + светлая рамка тела + ЛЕВАЯ пунктирная рейка «повтор»
 * (канон-скрин АвтоБП-пробник); Parallel = веер как IfElse, но БЕЗ бара
 * условия — OPEN: в корпусе etalons ParallelActivity не встречается,
 * эмит — экстраполяция IfElse (калибровка в T7, честная пометка в
 * docs/diagram.md). Зелёные капсулы «Начало»/«Конец» на верхушке/хвосте
 * хребта. templateData непуст → левая колонка блока данных (Параметры/
 * Переменные/Константы, записи «Name — Type»); DOCUMENT_FIELDS в .bpt
 * есть, НЕ рисуется (решение владельца: не просили, шум).
 *
 * Детерминизм: все координаты Math.round, порядок обхода = порядок
 * дерева, никакой среды/времени в выходе. Golden-тесты (T7) гоняют
 * палитру 'test' — смена дефолтной палитры золото не переписывает.
 */
'use strict';

// ---------- геометрия (детерминированная) ----------
const BOX_W = 200;
const LINE_H = 13;
const BOX_PAD_Y = 10;
const CHIP_H = 24;
const GHOST_H = 30;
const CAPSULE_W = 96;
const CAPSULE_H = 24;
const V_GAP = 26;
const H_GAP = 32;
const FAN_DROP = 12;
const RAIL_OUT = 16;
const RAIL_INSET = 9;    // рейка петли внутри рамки (правка владельца №4)
const FRAME_PAD = 12;
const MARGIN = 24;
const MAX_CHARS = 26;
const DATA_W = 210;

// ---------- палитры ----------
// Тёплый v0 «как в дизайнере» (мокап T8 уточнит); test — плоская серая
// для golden-снапшотов.
const PALETTES = {
  default: {
    canvas: '#ffffff',
    activityFill: '#fdf3d8', activityStroke: '#e0a63c', activityText: '#4a3a12',
    barFill: '#f6e3a1', barStroke: '#c9a227', barText: '#4a3a12',
    chipFill: '#f9edc7', chipStroke: '#d9b23a', chipText: '#4a3a12',
    frameStroke: '#d7dee5',
    startFill: '#dff0d8', startStroke: '#4caf50', startText: '#1b5e20',
    ghostStroke: '#c62828', ghostText: '#8e1f1f',
    addedFill: '#d5f5d5', addedStroke: '#2e7d32',
    modifiedFill: '#fff3cd', modifiedStroke: '#b8860b',
    edge: '#b6c2ce', edgeLabel: '#8a97a3',
    dataFill: '#f4f6f8', dataStroke: '#c8d0d8', dataText: '#42505c',
    subtitle: '#9aa6b1',
  },
  test: {
    canvas: '#ffffff',
    activityFill: '#eeeeee', activityStroke: '#555555', activityText: '#111111',
    barFill: '#dddddd', barStroke: '#444444', barText: '#111111',
    chipFill: '#e6e6e6', chipStroke: '#666666', chipText: '#111111',
    frameStroke: '#bbbbbb',
    startFill: '#f0f0f0', startStroke: '#222222', startText: '#000000',
    ghostStroke: '#990000', ghostText: '#660000',
    addedFill: '#ddffdd', addedStroke: '#006600',
    modifiedFill: '#ffffcc', modifiedStroke: '#aa6600',
    edge: '#999999', edgeLabel: '#777777',
    dataFill: '#f2f2f2', dataStroke: '#aaaaaa', dataText: '#222222',
    subtitle: '#888888',
  },
  neutral: {
    canvas: '#ffffff',
    activityFill: '#eef2f7', activityStroke: '#7a8ba3', activityText: '#2c3a4a',
    barFill: '#dfe7f0', barStroke: '#5a7391', barText: '#2c3a4a',
    chipFill: '#e8edf3', chipStroke: '#8296ad', chipText: '#2c3a4a',
    frameStroke: '#ccd5de',
    startFill: '#e1efe0', startStroke: '#66a266', startText: '#1e4620',
    ghostStroke: '#b34040', ghostText: '#7d3333',
    addedFill: '#dcefdc', addedStroke: '#3e7d4d',
    modifiedFill: '#f2ecd4', modifiedStroke: '#a08a3c',
    edge: '#c3ccd5', edgeLabel: '#8d99a6',
    dataFill: '#f5f6f8', dataStroke: '#c9d0d8', dataText: '#3d4854',
    subtitle: '#93a0ad',
  },
  // ночная тема вьюера (дефолт, слово владельца 2026-10-16); день = warm
  night: {
    canvas: '#1e242c',
    activityFill: '#2b3540', activityStroke: '#8fa3b8', activityText: '#dbe4ec',
    barFill: '#35414f', barStroke: '#a3b7c9', barText: '#e2eaf1',
    chipFill: '#303c48', chipStroke: '#93a7bb', chipText: '#dbe4ec',
    frameStroke: '#46525f',
    startFill: '#24402a', startStroke: '#5fa06b', startText: '#c9e6cd',
    ghostStroke: '#c96a6a', ghostText: '#d89a9a',
    addedFill: '#23492e', addedStroke: '#5fae74',
    modifiedFill: '#4a4325', modifiedStroke: '#c0a94e',
    edge: '#4d5a67', edgeLabel: '#7d8a97',
    dataFill: '#262f38', dataStroke: '#3d4854', dataText: '#b9c4ce',
    subtitle: '#6f7d8a',
  },
};

// ---------- текст ----------
function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&apos;');
}

function clipLine(s, max) {
  s = String(s == null ? '' : s);
  return s.length <= max ? s : s.slice(0, max - 1) + '…';
}

function wrapTitle(title) {
  const words = String(title == null ? '' : title).split(/\s+/)
    .filter((w) => w.length);
  if (!words.length) return [''];
  const lines = [''];
  for (const w of words) {
    const cur = lines[lines.length - 1];
    const cand = cur ? cur + ' ' + w : w;
    if (cand.length <= MAX_CHARS) {
      lines[lines.length - 1] = cand;
    } else if (lines.length < 2) {
      lines.push(w.length > MAX_CHARS ? clipLine(w, MAX_CHARS) : w);
    } else {
      lines[1] = clipLine(lines[1] + ' ' + w, MAX_CHARS);
      break;
    }
  }
  return lines.slice(0, 2);
}

// ---------- классификация ----------
function kindOf(type) {
  switch (type) {
    case 'SequentialWorkflowActivity': return 'root';
    case 'IfElseActivity': return 'ifelse';
    case 'IfElseBranchActivity': return 'branch';
    case 'WhileActivity':
    case 'ForEachActivity': return 'loop';
    case 'ParallelActivity': return 'parallel';
    case 'SequenceActivity':
    case 'SequentialActivity': return 'seq';
    default: return 'activity';
  }
}

function subtreeCount(node) {
  let n = 0;
  const stack = [node];
  while (stack.length) {
    const cur = stack.pop();
    if (cur && typeof cur === 'object') {
      if (!cur.ghost) n += 1;
      (cur.children || []).forEach((c) => stack.push(c));
    }
  }
  return n;
}

/**
 * Фактический разброс нарисованного после метки: боксы контента + рамки
 * детей (родительская рамка обнимает max(контент, детские рамки) + pad —
 * зазор до дочерней рамки всегда ≥10px, слово владельца 2026-10-16).
 * Возвращает {minX, maxX} или null.
 */
function extentsSince(ctx, mark) {
  let minX = Infinity;
  let maxX = -Infinity;
  for (const k of Object.keys(ctx.coords).slice(mark.c)) {
    if (k.startsWith('data:')) continue;
    const b = ctx.coords[k];
    minX = Math.min(minX, b.x);
    maxX = Math.max(maxX, b.x + b.w);
  }
  for (const fr of ctx.frameRects.slice(mark.f)) {
    minX = Math.min(minX, fr.x);
    maxX = Math.max(maxX, fr.x + fr.w);
  }
  return minX === Infinity ? null : { minX, maxX };
}

function newMark(ctx) {
  return { c: Object.keys(ctx.coords).length, f: ctx.frameRects.length };
}

// ---------- ядро лейаута ----------
/**
 * Каждый layout* возвращает {top, bottom, outX, outY}:
 *   top    — y входа поддерева (сюда приходит входящее ребро);
 *   bottom — y низа поддерева (отсюда стартует исходящее ребро);
 *   outX/outY — точка исходящего хребта (merge-точка, низ рамки...).
 */
/**
 * Двухпроходный лейаут: pass1 measure без эмиссии (extents дерева),
 * pass2 с эмиссией и сдвигом dx — канва авторазмером по реальному
 * разбросу (веер шире BOX_W уезжает за treeX; блок данных слева не
 * перекрывается — грабли первого рендера 2026-10-16).
 */
function buildLayout(tree, paint, foldState, opts, dx, emit) {
  if (!tree || !tree.root || typeof tree.root !== 'object') {
    throw new Error('tree.root отсутствует — контракт load_tree нарушен');
  }
  paint = paint || {};
  foldState = Array.isArray(foldState) ? foldState : [];
  const folded = new Set(foldState);
  opts = opts || {};
  const pal = PALETTES[opts.palette]
    || (opts.palette && typeof opts.palette === 'object' ? opts.palette
      : PALETTES.default);
  const ctx = {
    pal, paint, folded, emit,
    coords: {},
    index: {},
    frameRects: [],
    // слой рамок отдельно: стрелки → рамки → узлы (рамка не ложится на
    // стрелку — грабли мокапа (волна 7, 2026-10-16)
    frames: emit ? [] : { push() {} },
    shapes: emit ? [] : { push() {} },
    edges: emit ? [] : { push() {} },
    depthMax: 0,
    count: 0,
  };

  const root = tree.root;
  const kids = root.children || [];
  const spineX = dx + Math.round(BOX_W / 2);

  placeCapsule(spineX, MARGIN, 'Начало', ctx);
  let py = MARGIN + CAPSULE_H;
  let px = spineX;
  for (const k of kids) {
    const top = py + V_GAP;
    const sub = layoutSub(k, dx, top, ctx, 1);
    edge(ctx, px, py, spineX, sub.top, null);
    edgeArrow(ctx, spineX, sub.top);
    py = sub.bottom;
    px = sub.outX != null ? sub.outX : spineX;
  }
  const endTop = py + V_GAP;
  edge(ctx, px, py, spineX, endTop, null);
  edgeArrow(ctx, spineX, endTop);
  placeCapsule(spineX, endTop, 'Конец', ctx);
  ctx.treeBottom = endTop + CAPSULE_H;
  return ctx;
}

function layout(tree, paint, foldState, opts) {
  opts = opts || {};
  // pass 1: measure (дерево от x=0); рамки учитываются наравне с узлами —
  // они вылезают влево на FRAME_PAD+RAIL_OUT и без учёта наезжают на блок
  // данных (грабли «переменные поверх рамки», booking-chain_v17)
  const m = buildLayout(tree, paint, foldState, opts, 0, false);
  let minX = Infinity;
  let maxX = -Infinity;
  for (const k of Object.keys(m.coords)) {
    if (k.startsWith('data:')) continue;
    const b = m.coords[k];
    minX = Math.min(minX, b.x);
    maxX = Math.max(maxX, b.x + b.w);
  }
  for (const fr of m.frameRects) {
    minX = Math.min(minX, fr.x);
    maxX = Math.max(maxX, fr.x + fr.w);
  }
  const td = tree.templateData;
  const hasData = td && typeof td === 'object' &&
    (('parameters' in td && td.parameters.length)
      || ('variables' in td && td.variables.length)
      || ('constants' in td && td.constants.length));
  const dx = MARGIN + (hasData ? DATA_W + H_GAP : 0) - minX;
  // pass 2: эмиссия со сдвигом
  const ctx = buildLayout(tree, paint, foldState, opts, dx, true);
  if (hasData) {
    const dataBlock = layoutDataBlock(td, ctx);
    dataBlock.place(MARGIN, MARGIN);
    ctx.coords['@data'] = { x: MARGIN, y: MARGIN, w: dataBlock.w,
      h: dataBlock.h };
    ctx.index['@data'] = { x: MARGIN, y: MARGIN, w: dataBlock.w,
      h: dataBlock.h, kind: 'data-block', cls: '', title: 'Данные шаблона',
      type: 'data-block', name: '@data', foldable: false };
  }
  const dataRight = hasData ? MARGIN + DATA_W : 0;
  const canvasW = Math.round(Math.max(maxX + dx, dataRight) + MARGIN);
  const canvasH = Math.round(Math.max(ctx.treeBottom,
    hasData ? MARGIN + layoutDataBlockH(td) : 0) + MARGIN);
  const svg = svgHeader(canvasW, canvasH, ctx.pal)
    + ctx.edges.join('\n') + '\n' + ctx.frames.join('\n') + '\n'
    + ctx.shapes.join('\n') + '\n</svg>\n';
  return {
    svg,
    coords: ctx.coords,
    nodeIndex: ctx.index,
    stats: { nodes: ctx.count, depth: ctx.depthMax, w: canvasW, h: canvasH },
  };
}

function layoutDataBlockH(td) {
  if (!td || typeof td !== 'object') return 0;
  const HEAD_H = 22;
  const ITEM_H = 18;
  let h = 0;
  for (const key of ['parameters', 'variables', 'constants']) {
    const items = td[key] || [];
    if (!items.length) continue;
    h += HEAD_H + items.length * ITEM_H + 8 + 12;
  }
  return h ? h - 12 : 0;
}

function placeCapsule(cx, y, label, ctx) {
  const x = Math.round(cx - CAPSULE_W / 2);
  const key = label === 'Начало' ? '@start' : '@end';
  const p = ctx.pal;
  ctx.coords[key] = { x, y, w: CAPSULE_W, h: CAPSULE_H };
  ctx.index[key] = { x, y, w: CAPSULE_W, h: CAPSULE_H,
    kind: label === 'Начало' ? 'start' : 'end', cls: '', title: label,
    type: label === 'Начало' ? 'start' : 'end', name: key, foldable: false };
  ctx.shapes.push(
    `<g data-kind="${label === 'Начало' ? 'start' : 'end'}" ` +
    `data-name="${esc(key)}">` +
    `<rect x="${x}" y="${y}" width="${CAPSULE_W}" height="${CAPSULE_H}" ` +
    `rx="${CAPSULE_H / 2}" fill="${p.startFill}" stroke="${p.startStroke}"/>` +
    `<text x="${cx}" y="${y + CAPSULE_H / 2 + 4}" text-anchor="middle" ` +
    `font-size="12" fill="${p.startText}">${esc(label)}</text>` +
    `<title>${esc(label)}</title></g>`);
}

function layoutSub(node, x, y, ctx, depth) {
  ctx.depthMax = Math.max(ctx.depthMax, depth);
  if (node.ghost) return layoutGhost(node, x, y, ctx);
  const kind = kindOf(node.type);
  const cls = ctx.paint[node.name] || '';
  const foldable = kind === 'loop' || kind === 'ifelse' || kind === 'parallel'
    || kind === 'branch' || kind === 'activity';
  if (ctx.folded.has(node.name) && foldable) {
    return layoutFolded(node, x, y, ctx, cls);
  }
  switch (kind) {
    case 'ifelse': return layoutIfElse(node, x, y, ctx, depth, cls);
    case 'parallel': return layoutParallel(node, x, y, ctx, depth, cls);
    case 'loop': return layoutLoop(node, x, y, ctx, depth, cls);
    case 'branch': return layoutBranchBody(node, x, y, ctx, depth, cls);
    case 'seq': return layoutSeq(node, x, y, ctx, depth);
    default: return layoutPlain(node, x, y, ctx, depth, cls);
  }
}

// ---------- SequenceActivity: ПРОЗРАЧНЫЙ (решение владельца 2026-10-16 —
// в дизайнере не показывается) ----------

function layoutSeq(node, x, y, ctx, depth) {
  const cx = Math.round(x + BOX_W / 2);
  ctx.count += 1;
  const kids = node.children || [];
  if (!kids.length) {
    return { top: y, bottom: y, outX: cx, outY: y };
  }
  let top = null;
  let bottom = y;
  let lastOutY = y;
  let lastOutX = cx;
  for (const k of kids) {
    const sub = layoutSub(k, x, bottom + V_GAP, ctx, depth + 1);
    if (top === null) {
      top = sub.top;  // вход seq = вход первого ребёнка (родитель уже ребро провёл)
    } else {
      edge(ctx, lastOutX, lastOutY, cx, sub.top, null);
      edgeArrow(ctx, cx, sub.top);
    }
    bottom = sub.bottom;
    lastOutY = sub.outY;
    lastOutX = sub.outX != null ? sub.outX : cx;
  }
  return { top, bottom, outX: lastOutX, outY: lastOutY };
}

/**
 * Сухой замер поддерева (без эмиссии и без загрязнения coords): зона ветки
 * в дизайнере расширяется до размеров самого широкого поддерева (слово
 * владельца 2026-10-16, кейс «Есть CASH = заполнено» с подусловиями —
 * вложенный веер не должен наезжать на соседние колонки).
 * → {left, right} относительно точки укладки (left может быть < 0).
 */
function measureSub(node, ctx, depth) {
  const mctx = {
    pal: ctx.pal, paint: ctx.paint, folded: ctx.folded, emit: false,
    coords: {}, index: {}, frameRects: [],
    frames: { push() {} }, shapes: { push() {} }, edges: { push() {} },
    depthMax: 0, count: 0,
  };
  layoutSub(node, 0, 0, mctx, depth);
  let minX = Infinity;
  let maxX = -Infinity;
  for (const [k, b] of Object.entries(mctx.coords)) {
    if (k.startsWith('data:')) continue;
    minX = Math.min(minX, b.x);
    maxX = Math.max(maxX, b.x + b.w);
  }
  return minX === Infinity ? { left: 0, right: BOX_W }
    : { left: minX, right: maxX };
}

// ---------- обычный узел ----------
function layoutPlain(node, x, y, ctx, depth, cls) {
  const p = ctx.pal;
  const lines = wrapTitle(node.title || node.type);
  const h = BOX_PAD_Y * 2 + lines.length * LINE_H;
  const kids = node.children || [];
  const fill = cls === 'added' ? p.addedFill
    : cls === 'modified' ? p.modifiedFill : p.activityFill;
  const stroke = cls === 'added' ? p.addedStroke
    : cls === 'modified' ? p.modifiedStroke : p.activityStroke;
  ctx.count += 1;
  ctx.coords[node.name] = { x, y, w: BOX_W, h };
  ctx.index[node.name] = { x, y, w: BOX_W, h, kind: 'activity', cls,
    title: node.title, type: node.type, name: node.name,
    foldable: kids.length > 0 };
  ctx.shapes.push(nodeGroup(node, x, y, BOX_W, h, fill, stroke,
    p.activityText, lines, cls, 'activity', p,
    kids.length ? foldIconSvg(node.name, x, y, BOX_W, h, false, p) : ''));
  const cx = Math.round(x + BOX_W / 2);
  let bottom = y + h;
  if (kids.length) {
    // generic-контейнер: светлая рамка ПО РАЗБРОСУ детей (веер шире узла —
    // рамка его обнимает), слой frames — под стрелками узлы сверху
    const mark = newMark(ctx);
    const bodyX = x;
    let by = bottom + V_GAP;
    let lastOutY = bottom;
    let lastOutX = cx;
    for (const k of kids) {
      const top = by;
      const sub = layoutSub(k, bodyX, top, ctx, depth + 1);
      edge(ctx, lastOutX, lastOutY, cx, sub.top, null);
      edgeArrow(ctx, cx, sub.top);
      by = sub.bottom + V_GAP;
      lastOutY = sub.outY;
      lastOutX = sub.outX != null ? sub.outX : cx;
    }
    const frameBottom = by - V_GAP + FRAME_PAD;
    const ext = extentsSince(ctx, mark);
    const frameL = (ext ? ext.minX : x) - FRAME_PAD;
    const frameR = (ext ? ext.maxX : x + BOX_W) + FRAME_PAD;
    // верхняя кромка — от середины блока (канон While; правка владельца)
    const frameTop = y + Math.round(h / 2);
    const frameRect = { x: frameL, y: frameTop, w: frameR - frameL,
      h: frameBottom - frameTop };
    ctx.frames.push(`<rect x="${frameRect.x}" y="${frameRect.y}" ` +
      `width="${frameRect.w}" height="${frameRect.h}" rx="4" ` +
      `fill="none" stroke="${p.frameStroke}"/>`);
    ctx.frameRects.push(frameRect);
    bottom = frameBottom;
  }
  return { top: y, bottom, outX: cx, outY: bottom };
}

// ---------- ветка IfElse (чип-лейбл + субтитр условия + колонка детей) ---
function layoutBranchBody(node, x, y, ctx, depth, cls) {
  const p = ctx.pal;
  const chipCx = Math.round(x + BOX_W / 2);
  ctx.count += 1;
  ctx.coords[node.name] = { x, y, w: BOX_W, h: CHIP_H };
  ctx.index[node.name] = { x, y, w: BOX_W, h: CHIP_H, kind: 'branch', cls,
    title: node.title, type: node.type, name: node.name, foldable: true };
  const subH = node.subtitle ? 16 : 0;
  ctx.shapes.push(
    `<g data-kind="branch" data-name="${esc(node.name)}" ` +
    `data-class="${cls}" data-type="${esc(node.type)}">` +
    `<rect x="${x}" y="${y}" width="${BOX_W}" height="${CHIP_H}" rx="10" ` +
    `fill="${p.chipFill}" stroke="${p.chipStroke}"/>` +
    `<text x="${chipCx}" y="${y + CHIP_H / 2 + 4}" text-anchor="middle" ` +
    `font-size="11.5" fill="${p.chipText}">` +
    `${esc(clipLine(node.title || node.name || '', MAX_CHARS))}</text>` +
    (node.subtitle
      ? `<text x="${chipCx}" y="${y + CHIP_H + 12}" text-anchor="middle" ` +
        `font-size="10" fill="${p.subtitle}">` +
        `${esc(clipLine(node.subtitle, MAX_CHARS + 8))}</text>`
      : '') +
    `<title>${esc(node.name)} (${esc(node.type)})${cls ? ' · ' + cls : ''} ` +
    `${esc(node.title || '')}` +
    `${node.subtitle ? ' — условие: ' + esc(node.subtitle) : ''}</title>` +
    ((node.children || []).length
      ? foldIconSvg(node.name, x, y, BOX_W, CHIP_H, false, p)
      : '') + '</g>');
  let bottom = y + CHIP_H + subH;
  const kids = node.children || [];
  let lastOutY = y + CHIP_H;
  let lastOutX = chipCx;
  for (const k of kids) {
    const top = bottom + V_GAP;
    const sub = layoutSub(k, x, top, ctx, depth + 1);
    edge(ctx, lastOutX, lastOutY, chipCx, sub.top, null);
    edgeArrow(ctx, chipCx, sub.top);
    bottom = sub.bottom;
    lastOutY = sub.outY;
    lastOutX = sub.outX != null ? sub.outX : chipCx;
  }
  return { top: y, bottom, outX: chipCx, outY: lastOutY };
}

// ---------- веер (IfElse c баром / Parallel без бара) ----------
function layoutFan(node, x, y, ctx, depth, cls, withBar, kindLabel) {
  const p = ctx.pal;
  const cx = Math.round(x + BOX_W / 2);
  const branches = node.children || [];
  const lines = wrapTitle(node.title || node.type);
  const h = BOX_PAD_Y * 2 + lines.length * LINE_H;
  const fill = cls === 'modified' ? p.modifiedFill
    : cls === 'added' ? p.addedFill : (withBar ? p.barFill : p.activityFill);
  const stroke = cls === 'modified' ? p.modifiedStroke
    : cls === 'added' ? p.addedStroke
      : (withBar ? p.barStroke : p.activityStroke);
  const textColor = withBar ? p.barText : p.activityText;
  ctx.count += 1;
  ctx.coords[node.name] = { x, y, w: BOX_W, h };
  ctx.index[node.name] = { x, y, w: BOX_W, h, kind: kindLabel, cls,
    title: node.title, type: node.type, name: node.name,
    foldable: !withBar };
  ctx.shapes.push(nodeGroup(node, x, y, BOX_W, h, fill, stroke, textColor,
    lines, cls, kindLabel, p,
    branches.length
      ? foldIconSvg(node.name, x, y, BOX_W, h, false, p) : ''));
  if (!branches.length) {
    return { top: y, bottom: y + h, outX: cx, outY: y + h };
  }
  // адаптивные зоны: ширина ветки = её фактический разброс (dry-замер),
  // веер центрируется на спайне бара — вложенные веера не наезжают
  // на соседние колонки (канон дизайнера)
  const measures = branches.map((b) => measureSub(b, ctx, depth + 1));
  const widths = measures.map((m) => m.right - m.left);
  const totalW = widths.reduce((a, b) => a + b, 0)
    + (branches.length - 1) * H_GAP;
  const fanLeft = cx - Math.round(totalW / 2);
  // ножка от бара → горизонталь веера → ножка вниз → стрелка в чип
  // (гэп между горизонталью и блоками веток — правка владельца №6)
  const railY = y + h + FAN_DROP;
  const fanTop = railY + FAN_DROP;
  edge(ctx, cx, y + h, cx, railY, null);
  const bottoms = [];
  let maxBottom = fanTop;
  let cursor = fanLeft;
  for (let i = 0; i < branches.length; i++) {
    const bx = cursor - measures[i].left;
    const bcx = bx + Math.round(BOX_W / 2);
    const sub = layoutSub(branches[i], bx, fanTop, ctx, depth + 1);
    edge(ctx, cx, railY, bcx, railY, null);
    edge(ctx, bcx, railY, bcx, fanTop, null);
    edgeArrow(ctx, bcx, fanTop);
    bottoms.push({ x: bcx, y: sub.bottom });
    maxBottom = Math.max(maxBottom, sub.bottom);
    cursor += widths[i] + H_GAP;
  }
  const mergeY = maxBottom + V_GAP;
  const xs = bottoms.map((b) => b.x);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  for (const b of bottoms) edge(ctx, b.x, b.y, b.x, mergeY, null);
  if (maxX > minX) edge(ctx, minX, mergeY, maxX, mergeY, null);
  ctx.shapes.push(`<circle cx="${cx}" cy="${mergeY}" r="3" ` +
    `fill="${p.edge}"/>`);
  ctx.index[`@merge:${node.name}`] = { x: cx, y: mergeY, kind: 'merge',
    cls: '', title: '', type: 'merge', name: `@merge:${node.name}`,
    foldable: false };
  // отступ от merge-горизонтали до следующего блока (правка владельца №6 —
  // без него слипались)
  return { top: y, bottom: mergeY + 10, outX: cx, outY: mergeY + 10 };
}

function layoutIfElse(node, x, y, ctx, depth, cls) {
  return layoutFan(node, x, y, ctx, depth, cls, true, 'condition');
}

function layoutParallel(node, x, y, ctx, depth, cls) {
  return layoutFan(node, x, y, ctx, depth, cls, false, 'parallel');
}

// ---------- While/ForEach: заголовок + рамка + левая рейка «повтор» ------
function layoutLoop(node, x, y, ctx, depth, cls) {
  const p = ctx.pal;
  const lines = wrapTitle(node.title || node.type);
  const h = BOX_PAD_Y * 2 + lines.length * LINE_H;
  const headCx = Math.round(x + BOX_W / 2);
  ctx.count += 1;
  ctx.coords[node.name] = { x, y, w: BOX_W, h };
  ctx.index[node.name] = { x, y, w: BOX_W, h, kind: 'loop', cls,
    title: node.title, type: node.type, name: node.name, foldable: true };
  const fill = cls === 'modified' ? p.modifiedFill
    : cls === 'added' ? p.addedFill : p.activityFill;
  const stroke = cls === 'modified' ? p.modifiedStroke
    : cls === 'added' ? p.addedStroke : p.activityStroke;
  const kids = node.children || [];
  ctx.shapes.push(nodeGroup(node, x, y, BOX_W, h, fill, stroke,
    p.activityText, lines, cls, 'loop', p,
    kids.length ? foldIconSvg(node.name, x, y, BOX_W, h, false, p) : ''));
  if (!kids.length) {
    return { top: y, bottom: y + h, outX: headCx, outY: y + h };
  }
  let by = y + h + V_GAP;
  let lastOutY = y + h;
  let lastOutX = headCx;
  let bodyTop = by;
  const mark = newMark(ctx);
  for (const k of kids) {
    const top = by;
    const sub = layoutSub(k, x, top, ctx, depth + 1);
    edge(ctx, lastOutX, lastOutY, headCx, sub.top, null);
    edgeArrow(ctx, headCx, sub.top);
    by = sub.bottom + V_GAP;
    lastOutY = sub.outY;
    lastOutX = sub.outX != null ? sub.outX : headCx;
  }
  const bodyBottom = by - V_GAP;
  // рамка по фактическому разбросу тела (веер внутри цикла шире заголовка);
  // верхняя кромка — от середины заголовка (правка владельца №5), слева
  // резервируем полосу под рейку ВНУТРИ рамки (правка №4 — снаружи она
  // лезла на соседние ветки)
  const ext = extentsSince(ctx, mark);
  const frameL = (ext ? ext.minX : x) - FRAME_PAD - RAIL_OUT;
  const frameR = (ext ? ext.maxX : x + BOX_W) + FRAME_PAD;
  const frameTop = y + Math.round(h / 2);
  const frameBottom = bodyBottom + FRAME_PAD;
  const frameRect = { x: frameL, y: frameTop, w: frameR - frameL,
    h: frameBottom - frameTop };
  ctx.frames.push(`<rect x="${frameRect.x}" y="${frameRect.y}" ` +
    `width="${frameRect.w}" height="${frameRect.h}" rx="6" ` +
    `fill="none" stroke="${p.frameStroke}"/>`);
  ctx.frameRects.push(frameRect);
  // левая рейка ВНУТРИ рамки: от слияния тела влево-вверх и в левую кромку
  // заголовка (вход ниже верхней кромки рамки — на 3/4 высоты заголовка)
  const railX = frameL + RAIL_INSET;
  const mergeY = bodyBottom + 4;
  const headEntryY = y + Math.round(h * 3 / 4);
  const labelY = Math.round((bodyTop + bodyBottom) / 2);
  ctx.edges.push(`<path d="M ${headCx} ${mergeY} L ${railX} ${mergeY} ` +
    `L ${railX} ${headEntryY} L ${x} ${headEntryY}" fill="none" ` +
    `stroke="${p.edge}" stroke-dasharray="4 3"/>`);
  ctx.edges.push(`<text x="${railX + 10}" y="${labelY}" text-anchor="middle" ` +
    `font-size="10" fill="${p.edgeLabel}" ` +
    `transform="rotate(-90 ${railX + 10} ${labelY})">повтор</text>`);
  ctx.edges.push(`<polygon points="${x - 5},${headEntryY - 4} ` +
    `${x - 5},${headEntryY + 4} ${x},${headEntryY}" fill="${p.edge}"/>`);
  return { top: y, bottom: frameBottom, outX: headCx, outY: frameBottom };
}

// ---------- свёрнутый контейнер ----------
function layoutFolded(node, x, y, ctx, cls) {
  const n = subtreeCount(node);
  const label = `${clipLine(node.title || node.type, MAX_CHARS - 8)} (${n} узл.)`;
  const p = ctx.pal;
  ctx.count += 1;
  ctx.coords[node.name] = { x, y, w: BOX_W, h: CHIP_H };
  ctx.index[node.name] = { x, y, w: BOX_W, h: CHIP_H, kind: 'folded', cls,
    title: node.title, type: node.type, name: node.name, foldable: true,
    subtree: n };
  const fill = cls === 'modified' ? p.modifiedFill
    : cls === 'added' ? p.addedFill : p.chipFill;
  const stroke = cls === 'modified' ? p.modifiedStroke
    : cls === 'added' ? p.addedStroke : p.chipStroke;
  ctx.shapes.push(
    `<g data-kind="folded" data-name="${esc(node.name)}" ` +
    `data-class="${cls}" data-type="${esc(node.type)}">` +
    `<rect x="${x}" y="${y}" width="${BOX_W}" height="${CHIP_H}" rx="10" ` +
    `fill="${fill}" stroke="${stroke}"/>` +
    `<text x="${x + BOX_W / 2}" y="${y + CHIP_H / 2 + 4}" ` +
    `text-anchor="middle" font-size="11.5" fill="${p.chipText}">` +
    `${esc(label)} ▸</text>` +
    `<title>${esc(node.name)} (${esc(node.type)}) — ${n} узл. в поддереве` +
    `</title>` +
    `${foldIconSvg(node.name, x, y, BOX_W, CHIP_H, true, p)}</g>`);
  return { top: y, bottom: y + CHIP_H,
    outX: Math.round(x + BOX_W / 2), outY: y + CHIP_H };
}

// ---------- призрак удалённого ----------
function layoutGhost(node, x, y, ctx) {
  const p = ctx.pal;
  ctx.count += 1;
  ctx.coords[node.name] = { x, y, w: BOX_W, h: GHOST_H };
  ctx.index[node.name] = { x, y, w: BOX_W, h: GHOST_H, kind: 'ghost',
    cls: 'removed', title: node.title, type: 'removed', name: node.name,
    foldable: false, subtree: node.count };
  const label = `${clipLine(node.title || node.name || '', MAX_CHARS - 10)}` +
    ` — поддерево ${node.count || 1} узл.`;
  ctx.shapes.push(
    `<g data-kind="ghost" data-name="${esc(node.name)}" ` +
    `data-class="removed">` +
    `<rect x="${x}" y="${y}" width="${BOX_W}" height="${GHOST_H}" rx="6" ` +
    `fill="none" stroke="${p.ghostStroke}" stroke-dasharray="5 3"/>` +
    `<text x="${x + 8}" y="${y + GHOST_H / 2 + 4}" font-size="11" ` +
    `fill="${p.ghostText}">${esc(label)}</text>` +
    `<title>${esc(node.name)} — удалён в BASE; ${node.count || 1} узл.` +
    `</title></g>`);
  return { top: y, bottom: y + GHOST_H,
    outX: Math.round(x + BOX_W / 2), outY: y + GHOST_H };
}

// ---------- блок данных шаблона ----------
function layoutDataBlock(td, ctx) {
  if (!td || typeof td !== 'object') return null;
  const sections = [
    ['parameters', 'Параметры'],
    ['variables', 'Переменные'],
    ['constants', 'Константы'],
  ].map(([key, label]) => ({ key, label, items: td[key] || [] }))
    .filter((s) => s.items.length);
  if (!sections.length) return null;
  const HEAD_H = 22;
  const ITEM_H = 18;
  let h = 0;
  const pieces = [];
  for (const s of sections) {
    const boxH = HEAD_H + s.items.length * ITEM_H + 8;
    pieces.push({ s, y: h, boxH, HEAD_H, ITEM_H });
    h += boxH + 12;
  }
  const totalH = h - 12;
  return {
    w: DATA_W,
    h: totalH,
    place(px, py) {
      const p = ctx.pal;
      for (const piece of pieces) {
        const by = py + piece.y;
        ctx.shapes.push(
          `<g data-kind="template-data" data-section="${piece.s.key}">` +
          `<rect x="${px}" y="${by}" width="${DATA_W}" ` +
          `height="${piece.boxH}" rx="6" fill="${p.dataFill}" ` +
          `stroke="${p.dataStroke}"/>` +
          `<text x="${px + 10}" y="${by + 15}" font-size="11.5" ` +
          `font-weight="bold" fill="${p.dataText}">` +
          `${esc(piece.s.label)} (${piece.s.items.length})</text>` +
          `<title>${esc(piece.s.label)} шаблона</title></g>`);
        piece.s.items.forEach((it, i) => {
          const iy = by + piece.HEAD_H + i * piece.ITEM_H;
          const key = `data:${piece.s.key}:${it.name}`;
          const line = `${clipLine(it.name, 20)} — ${clipLine(it.type, 12)}`;
          ctx.coords[key] = { x: px, y: iy, w: DATA_W, h: piece.ITEM_H };
          ctx.index[key] = { x: px, y: iy, w: DATA_W, h: piece.ITEM_H,
            kind: 'template-data', cls: '', title: it.title || it.name,
            type: it.type, name: it.name, section: piece.s.key,
            required: it.required, def: it.default };
          ctx.shapes.push(
            `<g data-kind="template-data" data-section="${piece.s.key}" ` +
            `data-name="${esc(it.name)}">` +
            `<text x="${px + 18}" y="${iy + 13}" font-size="10.5" ` +
            `fill="${p.dataText}">${esc(line)}</text>` +
            `<title>${esc(it.name)} · ${esc(it.type)}` +
            `${it.required ? ' · обязателен' : ''}` +
            `${it.default != null ? ' · default: ' + esc(it.default) : ''}` +
            `</title></g>`);
        });
      }
    },
  };
}

// ---------- svg-примитивы ----------
function nodeGroup(node, x, y, w, h, fill, stroke, textColor, lines, cls,
  kind, pal, icon) {
  const cx = Math.round(x + w / 2);
  let t = '';
  if (lines.length === 1) {
    t = `<text x="${cx}" y="${y + h / 2 + 4}" text-anchor="middle" ` +
      `font-size="11.5" fill="${textColor}">${esc(lines[0])}</text>`;
  } else {
    t = `<text x="${cx}" y="${y + h / 2 - 2}" text-anchor="middle" ` +
      `font-size="11.5" fill="${textColor}">${esc(lines[0])}</text>` +
      `<text x="${cx}" y="${y + h / 2 + 11}" text-anchor="middle" ` +
      `font-size="11.5" fill="${textColor}">${esc(lines[1])}</text>`;
  }
  const sub = node.subtitle
    ? `<text x="${cx}" y="${y + h + 12}" text-anchor="middle" ` +
      `font-size="10" fill="${pal.subtitle}">` +
      `${esc(clipLine(node.subtitle, MAX_CHARS + 8))}</text>`
    : '';
  return `<g data-kind="${kind}" data-name="${esc(node.name)}" ` +
    `data-class="${cls}" data-type="${esc(node.type)}"` +
    `${node.subtitle ? ' data-subtitle="1"' : ''}>` +
    `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="6" ` +
    `fill="${fill}" stroke="${stroke}"/>${t}${sub}` +
    `<title>${esc(node.name)} (${esc(node.type)})` +
    `${cls ? ' · ' + cls : ''} ${esc(node.title || '')}</title>` +
    `${icon || ''}</g>`;
}

function edge(ctx, x1, y1, x2, y2, label) {
  const p = ctx.pal;
  x1 = Math.round(x1); y1 = Math.round(y1);
  x2 = Math.round(x2); y2 = Math.round(y2);
  // ортогональ «сначала горизонталь, потом вертикаль» — ход потока вниз
  const d = (x1 === x2 || y1 === y2)
    ? `M ${x1} ${y1} L ${x2} ${y2}`
    : `M ${x1} ${y1} L ${x2} ${y1} L ${x2} ${y2}`;
  ctx.edges.push(`<path d="${d}" fill="none" stroke="${p.edge}"/>`);
  if (label != null) {
    ctx.edges.push(`<text x="${Math.round((x1 + x2) / 2)}" ` +
      `y="${Math.round(y1) - 4}" text-anchor="middle" font-size="10" ` +
      `fill="${p.edgeLabel}">${esc(label)}</text>`);
  }
}

function edgeArrow(ctx, x, y) {
  const p = ctx.pal;
  ctx.edges.push(`<polygon points="${x - 4},${y - 5} ${x + 4},${y - 5} ` +
    `${x},${y}" fill="${p.edge}"/>`);
}

function svgHeader(w, h, pal) {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" ` +
    `height="${h}" viewBox="0 0 ${w} ${h}" data-diagram="bpt" ` +
    `font-family="'Segoe UI', Arial, sans-serif">` +
    `<rect x="0" y="0" width="${w}" height="${h}" fill="${pal.canvas}"/>`;
}

// ---------- иконка свёртки (правка владельца: клик по иконке — fold,
// клик по телу блока — инспекция; голый шеврон без кнопки — не attracts
// attention, слово владельца 2026-10-16) ----------
function foldIconSvg(name, bx, by, bw, bh, isFolded, pal) {
  const cx = bx + bw - 11;
  const cy = by + Math.round(bh / 2);
  const chev = isFolded
    ? `M ${cx - 2},${cy - 3} L ${cx + 2},${cy} L ${cx - 2},${cy + 3}`
    : `M ${cx - 3},${cy - 1} L ${cx},${cy + 2} L ${cx + 3},${cy - 1}`;
  // невидимый кругляш-хитзона: в шеврон голыми руками попадать тяжело
  // (правка владельца 2026-10-16)
  return `<g data-fold-icon="${esc(name)}">` +
    `<circle cx="${cx}" cy="${cy}" r="11" fill="transparent" ` +
    `stroke="none" pointer-events="all"/>` +
    `<path d="${chev}" fill="none" stroke="${pal.subtitle}" ` +
    `stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"` +
    ` pointer-events="none"/>` +
    `<title>${isFolded ? 'Развернуть' : 'Свернуть'}</title></g>`;
}

// ---------- CLI: node bpt_diagram_runtime.js tree.json [paint.json]
//                       [fold.json] [--palette=test|default] ----------
if (typeof require !== 'undefined' && require.main === module) {
  const fs = require('fs');
  const argv = process.argv.slice(2);
  const files = argv.filter((a) => !a.startsWith('--'));
  const paletteArg = (argv.find((a) => a.startsWith('--palette=')) || '')
    .split('=')[1];
  const wantJson = argv.includes('--json');
  if (!files.length) {
    process.stderr.write('usage: node bpt_diagram_runtime.js tree.json ' +
      '[paint.json] [fold.json] [--palette=test|default] > out.svg\n');
    process.exit(2);
  }
  const tree = JSON.parse(fs.readFileSync(files[0], 'utf8'));
  const paint = files[1] ? JSON.parse(fs.readFileSync(files[1], 'utf8')) : {};
  const fold = files[2] ? JSON.parse(fs.readFileSync(files[2], 'utf8')) : [];
  const t0 = Date.now();
  const res = layout(tree, paint, fold, { palette: paletteArg || 'default' });
  if (process.env.BPT_DIAGRAM_DEBUG === '1') {
    process.stderr.write(`[diagram] nodes=${res.stats.nodes} ` +
      `depth=${res.stats.depth} canvas=${res.stats.w}x${res.stats.h} ` +
      `ms=${Date.now() - t0}\n`);
  }
  if (wantJson) {
    process.stdout.write(JSON.stringify(res) + '\n');
  } else {
    process.stdout.write(res.svg);
  }
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { layout, esc, wrapTitle, kindOf, subtreeCount, PALETTES };
}
