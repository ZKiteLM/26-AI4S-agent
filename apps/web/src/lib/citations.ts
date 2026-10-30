/** Return a citation only when the pointer lands on its number glyphs. */
export function citationAtPoint(
  layer: HTMLElement,
  x: number,
  y: number,
): number | null {
  const nodes: { node: Text; start: number; end: number }[] = [];
  const walker = document.createTreeWalker(layer, NodeFilter.SHOW_TEXT);
  let text = "";
  while (walker.nextNode()) {
    const node = walker.currentNode as Text;
    if (!node.parentElement?.closest("span") || !node.data) continue;
    nodes.push({
      node,
      start: text.length,
      end: text.length + node.data.length,
    });
    text += node.data;
  }
  function position(index: number) {
    const part = nodes.find((item) => item.start <= index && index < item.end);
    return part ? { node: part.node, offset: index - part.start } : null;
  }
  for (const bracket of text.matchAll(/\[[\d\s,;–-]+\]/g)) {
    const start = bracket.index;
    for (const number of bracket[0].matchAll(/\d+/g)) {
      const from = position(start + number.index);
      const to = position(start + number.index + number[0].length - 1);
      if (!from || !to) continue;
      const range = document.createRange();
      range.setStart(from.node, from.offset);
      range.setEnd(to.node, to.offset + 1);
      for (const rect of range.getClientRects()) {
        if (
          x >= rect.left - 2 &&
          x <= rect.right + 2 &&
          y >= rect.top - 2 &&
          y <= rect.bottom + 2
        )
          return Number(number[0]);
      }
    }
  }
  return null;
}
