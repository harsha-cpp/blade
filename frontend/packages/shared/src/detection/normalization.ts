const INVISIBLE_CHARACTERS = [
  "\u00ad",
  "\u034f",
  "\u180e",
  "\u200b",
  "\u200c",
  "\u200d",
  "\u2060",
  "\ufeff",
];

const INVISIBLE_RANGES: readonly (readonly [number, number])[] = [
  [0x2061, 0x2064],
  [0xfe00, 0xfe0f],
];

const ZERO_WIDTH = new Set<string>(INVISIBLE_CHARACTERS);
for (const [first, last] of INVISIBLE_RANGES) {
  for (let code = first; code <= last; code += 1) {
    ZERO_WIDTH.add(String.fromCodePoint(code));
  }
}

const DECIMAL_DIGIT_BLOCK_STARTS = [
  0x0030, 0x0660, 0x06f0, 0x07c0, 0x0966, 0x09e6,
  0x0a66, 0x0ae6, 0x0b66, 0x0be6, 0x0c66, 0x0ce6,
  0x0d66, 0x0de6, 0x0e50, 0x0ed0, 0x0f20, 0x1040,
  0x1090, 0x17e0, 0x1810, 0x1946, 0x19d0, 0x1a80,
  0x1a90, 0x1b50, 0x1bb0, 0x1c40, 0x1c50, 0xa620,
  0xa8d0, 0xa900, 0xa9d0, 0xa9f0, 0xaa50, 0xabf0,
  0xff10, 0x104a0, 0x10d30, 0x11066, 0x110f0, 0x11136,
  0x111d0, 0x112f0, 0x11450, 0x114d0, 0x11650, 0x116c0,
  0x11730, 0x118e0, 0x11950, 0x11c50, 0x11d50, 0x11da0,
  0x11f50, 0x16a60, 0x16ac0, 0x16b50, 0x1d7ce, 0x1d7d8,
  0x1d7e2, 0x1d7ec, 0x1d7f6, 0x1e140, 0x1e2f0, 0x1e4f0,
  0x1e950, 0x1fbf0,
];

const MAX_ASCII_CODE_POINT = 0x7f;

export interface FoldedText {
  text: string;
  source: string;
  offsets: number[] | null;
}

function asciiDigit(character: string): string | null {
  const codePoint = character.codePointAt(0);
  if (codePoint === undefined) return null;
  for (const start of DECIMAL_DIGIT_BLOCK_STARTS) {
    const offset = codePoint - start;
    if (offset >= 0 && offset <= 9) return String(offset);
  }
  return null;
}

function foldCharacter(character: string): string {
  let folded = "";
  for (const candidate of character.normalize("NFKC")) {
    if (ZERO_WIDTH.has(candidate)) continue;
    folded += asciiDigit(candidate) ?? candidate;
  }
  return folded;
}

function isAscii(text: string): boolean {
  for (let index = 0; index < text.length; index += 1) {
    if (text.charCodeAt(index) > MAX_ASCII_CODE_POINT) return false;
  }
  return true;
}

export function fold(text: string): FoldedText {
  if (isAscii(text)) return { text, source: text, offsets: null };

  let folded = "";
  const offsets: number[] = [];
  let index = 0;
  for (const character of text) {
    const produced = foldCharacter(character);
    for (let unit = 0; unit < produced.length; unit += 1) offsets.push(index);
    folded += produced;
    index += character.length;
  }
  offsets.push(text.length);
  return { text: folded, source: text, offsets };
}

export function originalSpan(
  folded: FoldedText,
  start: number,
  end: number,
): [number, number] {
  if (!folded.offsets) return [start, end];
  return [folded.offsets[start]!, folded.offsets[end]!];
}
