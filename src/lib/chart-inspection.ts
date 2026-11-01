export function candleIndexAtPoint({ x, y, left, right, top, bottom, count }: { x: number; y: number; left: number; right: number; top: number; bottom: number; count: number }): number | null {
  if (!count || right <= left || x < left || x > right || y < top || y > bottom) return null;
  return Math.min(count - 1, Math.floor((x - left) / (right - left) * count));
}
