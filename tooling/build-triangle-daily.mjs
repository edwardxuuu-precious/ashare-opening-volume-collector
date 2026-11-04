import { createHash } from 'node:crypto';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';
import {
  loadTriangleData,
  parseTriangleCatalog,
} from '../src/lib/triangle-data.ts';
import {
  analyzeTriangles,
  defaultTriangleParams,
} from '../src/lib/triangles.ts';

const ROOT = fileURLToPath(new URL('../', import.meta.url));
const HISTORY = 140;
const RECENT = 5;
const MAX_BUNDLE_BYTES = 4 * 1024 * 1024;

export function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value !== null && typeof value === 'object')
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, canonical(value[key])]),
    );
  return value;
}

export function encode(value) {
  return Buffer.from(JSON.stringify(canonical(value)), 'utf8');
}

function sha256(bytes) {
  return createHash('sha256').update(bytes).digest('hex');
}

export async function implementationIdentity() {
  const files = {};
  for (const name of [
    'src/lib/triangles.ts',
    'src/lib/triangle-data.ts',
    'tooling/build-triangle-daily.mjs',
  ])
    files[name] = sha256(await readFile(path.join(ROOT, name)));
  return {
    rule_sha256: files['src/lib/triangles.ts'],
    execution_sha256: sha256(encode(files)),
    files,
    params: defaultTriangleParams,
  };
}

export async function build({ input, output, datasetId, asOf, source = {} }) {
  if (!/^[a-f0-9]{16}$/.test(datasetId)) throw Error('DATASET_ID_REQUIRED');
  const manifest = JSON.parse(
    await readFile(path.join(input, 'manifest.json'), 'utf8'),
  );
  const stockList = JSON.parse(
    await readFile(path.join(input, 'stocks.json'), 'utf8'),
  );
  const catalog = parseTriangleCatalog(manifest);
  if (!catalog.calendarKnown) throw Error('SOURCE_CALENDAR_REQUIRED');
  if (
    !Array.isArray(stockList.rows) ||
    !stockList.rows.length ||
    stockList.rows.length > 10000
  )
    throw Error('SOURCE_STOCK_LIST_REQUIRED');
  const codes = new Set();
  catalog.securities = stockList.rows
    .map((row) => {
      if (
        !row ||
        !/^\d{6}$/.test(row.code) ||
        codes.has(row.code) ||
        typeof row.name !== 'string'
      )
        throw Error('SOURCE_STOCK_IDENTITY');
      codes.add(row.code);
      return {
        code: row.code,
        name: row.name,
        market: typeof row.market === 'string' ? row.market : '',
      };
    })
    .sort((a, b) => a.code.localeCompare(b.code));
  catalog.universeTotal = catalog.securities.length;
  const detailSessions = catalog.sessions.filter((date) => date <= asOf);
  if (detailSessions.at(-1) !== asOf || detailSessions.length > 2000)
    throw Error('SOURCE_HISTORY_BOUND');
  const generations = new Map(
    manifest.dates.map((entry) => [entry.file, entry.generatedAt]),
  );
  const fetcher = async (url) => {
    const filename = new URL(url).pathname.split('/').at(-1);
    if (!/^\d{4}-\d{2}-\d{2}\.json$/.test(filename))
      throw Error('SOURCE_FILENAME');
    const raw = await readFile(path.join(input, filename));
    const day = JSON.parse(raw);
    if (
      !generations.has(filename) ||
      day.generatedAt !== generations.get(filename)
    )
      throw Error('SOURCE_GENERATION_CHANGED');
    return new Response(raw, {
      headers: { 'content-type': 'application/json' },
    });
  };
  const latest = JSON.parse(
    await readFile(path.join(input, `${asOf}.json`), 'utf8'),
  );
  if (
    !latest ||
    latest.scope !== 'full' ||
    latest.priceDataComplete !== true ||
    latest.pendingCount !== 0 ||
    !Array.isArray(latest.rows) ||
    latest.rows.length !== codes.size ||
    latest.rows.some((row) => !codes.has(row.code)) ||
    new Set(latest.rows.map((row) => row.code)).size !== codes.size
  )
    throw Error('SOURCE_LATEST_UNIVERSE_INCOMPLETE');
  const dataset = await loadTriangleData({
    dataBase: 'https://frozen.invalid',
    signal: new AbortController().signal,
    fetcher,
    catalog,
    asOf,
    historySessions: detailSessions.length,
    fullHistory: true,
    selection: { kind: 'all' },
    retryBaseMs: 0,
  });
  if (
    dataset.coverage.failedDates.some((entry) => catalog.saved.has(entry.date))
  )
    throw Error('FROZEN_SOURCE_READ_FAILED');
  const detailSeries = dataset.series;
  const series = detailSeries.filter((entry) => codes.has(entry.security.code));
  const sessions = detailSessions.slice(-HISTORY);
  const screenSeries = series.map((entry) => ({
    security: entry.security,
    bars: entry.bars.slice(-HISTORY),
  }));
  const coverage = {
    loadedDays: sessions.filter((date) => catalog.saved.has(date)).length,
    requestedDays: sessions.length,
    requestedHistory: HISTORY,
    failedDates: dataset.coverage.failedDates.filter((entry) =>
      sessions.includes(entry.date),
    ),
    calendarKnown: true,
    universeTotal: codes.size,
    completePriceSeries: screenSeries.filter((entry) =>
      entry.bars.every((bar) => bar.kind === 'bar'),
    ).length,
    validPriceBars: screenSeries.reduce(
      (sum, entry) =>
        sum + entry.bars.filter((bar) => bar.kind === 'bar').length,
      0,
    ),
  };
  const generatedAt = manifest.dates.find(
    (entry) => entry.date === asOf,
  )?.generatedAt;
  if (
    typeof generatedAt !== 'string' ||
    !Number.isFinite(Date.parse(generatedAt))
  )
    throw Error('SOURCE_GENERATED_AT_REQUIRED');
  const rows = screenSeries.map(({ security, bars }) => {
    const analysis = analyzeTriangles({
      bars,
      params: defaultTriangleParams,
      asOf,
    });
    const last = bars.at(-1);
    return {
      security,
      latestClose: last?.kind === 'bar' ? last.close : null,
      availability: analysis.availability,
      formations: analysis.formations.filter(
        (formation) =>
          formation.qualified &&
          (formation.state.kind === 'active' ||
            formation.state.kind === 'observing'),
      ),
    };
  });
  const screening = {
    schema: 'stock.triangles.daily.screen.v1',
    dataset_id: datasetId,
    as_of: asOf,
    generated_at: generatedAt,
    params: defaultTriangleParams,
    sessions,
    coverage,
    rows,
  };
  await mkdir(output, { recursive: true });
  async function save(file, value) {
    const raw = encode(value);
    await writeFile(path.join(output, file), raw);
    return { file, sha256: sha256(raw), bytes: raw.length };
  }
  const screen = await save('screening.json', screening);
  const bundles = [];
  for (let start = 0; start < detailSeries.length;) {
    let length = Math.min(100, detailSeries.length - start);
    let batch, value;
    while (length > 0) {
      batch = detailSeries.slice(start, start + length);
      value = {
        schema: 'stock.triangles.daily.bundle.v1',
        dataset_id: datasetId,
        as_of: asOf,
        sessions: detailSessions,
        rows: batch,
      };
      if (encode(value).length <= MAX_BUNDLE_BYTES) break;
      length = Math.floor(length / 2);
    }
    if (!length || bundles.length >= 1000)
      throw Error('DETAIL_BUNDLE_DECODED_BOUND');
    const file = `bundle-${String(bundles.length).padStart(3, '0')}.json`;
    bundles.push({
      ...(await save(file, value)),
      codes: batch.map((entry) => entry.security.code),
    });
    start += length;
  }
  const identity = await implementationIdentity();
  const publication = {
    schema: 'stock.triangles.daily.manifest.v1',
    dataset_id: datasetId,
    as_of: asOf,
    generated_at: generatedAt,
    params: defaultTriangleParams,
    history_sessions: HISTORY,
    recent_sessions: RECENT,
    sessions,
    detail_sessions: detailSessions,
    universe_total: codes.size,
    rule_sha256: identity.rule_sha256,
    source,
    screening: screen,
    bundles,
    history_only_securities: detailSeries
      .filter((entry) => !codes.has(entry.security.code))
      .map((entry) => entry.security),
  };
  await save('manifest.json', publication);
  return publication;
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href
) {
  const args = process.argv.slice(2);
  if (args.length === 1 && args[0] === '--identity')
    process.stdout.write(JSON.stringify(await implementationIdentity()) + '\n');
  else {
    const options = new Map();
    for (let index = 0; index < args.length; index += 2) {
      if (
        !['--input', '--out', '--dataset-id', '--as-of', '--source'].includes(
          args[index],
        ) ||
        !args[index + 1]
      )
        throw Error(
          'Use --input DIR --out DIR --dataset-id 16HEX --as-of YYYY-MM-DD [--source FILE]',
        );
      options.set(args[index], args[index + 1]);
    }
    const source = options.has('--source')
      ? JSON.parse(await readFile(options.get('--source'), 'utf8'))
      : {};
    const result = await build({
      input: options.get('--input'),
      output: options.get('--out'),
      datasetId: options.get('--dataset-id'),
      asOf: options.get('--as-of'),
      source,
    });
    process.stdout.write(
      JSON.stringify({
        dataset_id: result.dataset_id,
        as_of: result.as_of,
        universe_total: result.universe_total,
        detail_sessions: result.detail_sessions.length,
        bundles: result.bundles.length,
      }) + '\n',
    );
  }
}
