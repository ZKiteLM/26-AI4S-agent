export type ReaderBlock = {
  id: string;
  page_index: number;
  order: number;
  type: string;
  text: string;
  markdown: string;
  latex: string | null;
  bbox: number[];
};

export function nearestBlock(
  blocks: ReaderBlock[],
  x: number,
  y: number,
): ReaderBlock | null {
  let best: ReaderBlock | null = null;
  let score = Infinity;
  for (const block of blocks) {
    if (block.bbox.length !== 4 || block.type === "page_number") continue;
    const [left, top, right, bottom] = block.bbox;
    const dy = Math.max(top - y, 0, y - bottom);
    const dx = Math.max(left - x, 0, x - right);
    const next = dy * 4 + dx;
    if (next < score) {
      best = block;
      score = next;
    }
  }
  return best;
}
