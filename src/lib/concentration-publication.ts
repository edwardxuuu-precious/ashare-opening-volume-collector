import {
  parseConcentrationScan,
  parseConcentrationSource,
  type ConcentrationSource,
} from './concentration-screen.ts';

export const concentrationPublicationPath =
  '/data/chips/daily/concentration-publication.json';

export type ConcentrationPublication = ConcentrationSource & {
  source: ConcentrationSource;
  payload_key: string;
  input_fingerprint: string;
  payload_sha256: string;
};

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function object(value: unknown): Record<string, unknown> {
  if (!isObject(value))
    throw Error('集中度完整发布记录无效。');
  return value;
}

export function parseConcentrationPublication(value: unknown): ConcentrationPublication {
  const row = object(value);
  const source = parseConcentrationSource(row.source);
  const payload = `data/chips/daily/${source.active_slot}/concentration-two-v1.json`;
  if (
    row.schema !== 'stock.chips.concentration.publication.v1' ||
    row.dataset_id !== source.dataset_id || row.as_of !== source.as_of ||
    row.generated_at !== source.generated_at || row.payload_key !== payload ||
    typeof row.input_fingerprint !== 'string' || !/^[a-f0-9]{64}$/.test(row.input_fingerprint) ||
    typeof row.payload_sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(row.payload_sha256)
  ) throw Error('集中度完整发布记录与结果不匹配。');
  return {
    ...source, source, payload_key: payload,
    input_fingerprint: row.input_fingerprint, payload_sha256: row.payload_sha256,
  };
}

export function concentrationPublicationReader(
  read: (path: string, signal: AbortSignal) => Promise<unknown>,
) {
  const readManifest = async (signal: AbortSignal) =>
    parseConcentrationPublication(await read(concentrationPublicationPath, signal));
  return {
    readManifest,
    async readPayload(publication: ConcentrationPublication, signal: AbortSignal) {
      const raw = object(await read('/' + publication.payload_key, signal));
      const current = await readManifest(signal);
      if (JSON.stringify(current) !== JSON.stringify(publication))
        throw Error('集中度新结果正在发布，仍保留上次完整结果。');
      if (raw.input_fingerprint !== publication.input_fingerprint)
        throw Error('集中度结果与完整发布记录不匹配。');
      return parseConcentrationScan(raw, publication.source);
    },
  };
}
