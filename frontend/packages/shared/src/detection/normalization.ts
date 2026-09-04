const ZERO_WIDTH = new Set(["\u200b", "\u200c", "\u200d", "\ufeff"]);

const DECIMAL_DIGIT_BLOCK_STARTS = [
  0x0660, 0x06f0, 0x07c0, 0x0966, 0x09e6, 0x0a66, 0x0ae6, 0x0b66, 0x0be6,
  0x0c66, 0x0ce6, 0x0d66, 0x0de6, 0x0e50, 0x0ed0, 0x0f20, 0x1040, 0x1090,
  0x17e0, 0x1810, 0x1946, 0x19d0, 0x1a80, 0x1a90, 0x1b50, 0x1bb0, 0x1c40,
  0x1c50, 0xa620, 0xa8d0, 0xa900, 0xa9d0, 0xa9f0, 0xaa50, 0xabf0, 0xff10,
];

const MAX_ASCII_CODE_POINT = 0x7f;

export interface FoldedText {
  text: string;
  source: string;
  offsets: number[] | null;
}

export function asciiDigit(character: string): string | null {
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
    for (const produced of foldCharacter(character)) {
      folded += produced;
      offsets.push(index);
    }
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
