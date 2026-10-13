// Keep the supported Markdown readable even while the typewriter stops inside
// a delimiter. Literal hashes are never changed outside a Markdown heading.
type Delimiter = { start: number; end: number; token: string; open: boolean; close: boolean };

function cleanInlineMarkdown(text: string, plainText: boolean): string {
  const hidden = new Set<number>();
  const protectedCharacters = new Set<number>();
  const delimiters: Delimiter[] = [];

  for (let index = 0; index < text.length; index += 1) {
    if (text[index] === "\\" && index + 1 < text.length) {
      protectedCharacters.add(index);
      protectedCharacters.add(index + 1);
      if (plainText) hidden.add(index);
      index += 1;
      continue;
    }
    if (text[index] !== "`") continue;
    let end = index + 1;
    while (text[end] === "`") end += 1;
    const run = text.slice(index, end);
    let closing = text.indexOf(run, end);
    while (closing !== -1 && (text[closing - 1] === "`" || text[closing + run.length] === "`")) {
      closing = text.indexOf(run, closing + run.length);
    }
    if (closing === -1) {
      for (let position = index; position < end; position += 1) hidden.add(position);
      index = end - 1;
      continue;
    }
    const codeEnd = closing + run.length;
    for (let position = index; position < codeEnd; position += 1) {
      protectedCharacters.add(position);
      if (plainText && (position < end || position >= closing)) hidden.add(position);
    }
    index = codeEnd - 1;
  }

  for (const match of text.matchAll(/\*+|_+/g)) {
    const start = match.index;
    if (protectedCharacters.has(start)) continue;
    const end = start + match[0].length;
    const before = text[start - 1] ?? "";
    const after = text[end] ?? "";
    // An underscore within an identifier (snake_case) is literal text.
    if (match[0][0] === "_" && /[\p{L}\p{N}]/u.test(before) && /[\p{L}\p{N}]/u.test(after)) continue;
    delimiters.push({
      start, end, token: match[0],
      open: Boolean(after && !/\s/.test(after)),
      close: Boolean(before && !/\s/.test(before)),
    });
  }

  const pending = new Map<string, Delimiter>();
  const paired = new Set<number>();
  for (const delimiter of delimiters) {
    const opening = pending.get(delimiter.token);
    if (opening && delimiter.close) {
      paired.add(opening.start);
      paired.add(delimiter.start);
      pending.delete(delimiter.token);
    } else if (delimiter.open) {
      pending.set(delimiter.token, delimiter);
    }
  }
  for (const delimiter of delimiters) {
    if (plainText || !paired.has(delimiter.start)) {
      for (let position = delimiter.start; position < delimiter.end; position += 1) hidden.add(position);
    }
  }
  return text.split("").filter((_character, index) => !hidden.has(index)).join("");
}

function cleanPlainLinks(text: string): string {
  return text
    .replace(/!?\[([^\]]*)\]\([^\n]*?\)/g, "$1")
    .replace(/!?\[([^\]]*)\]\[[^\]]*\]/g, "$1")
    .replace(/<((?:https?:\/\/|mailto:)[^>]+)>/gi, "$1")
    .replace(/<\/?[A-Za-z][^>]*>/g, "");
}

function formatAssistantMarkdown(content: string, plainText: boolean, isTyping: boolean, trim = true): string {
  let fence: { character: string; length: number } | null = null;
  const lines: string[] = [];
  for (const line of content.replace(/\r\n?/g, "\n").split("\n")) {
    if (fence) {
      const closing = line.match(/^ {0,3}(`+|~+)[ \t]*$/);
      if (closing && closing[1][0] === fence.character && closing[1].length >= fence.length) {
        fence = null;
        if (!plainText) lines.push(line);
      } else if (isTyping && closing && closing[1][0] === fence.character) {
        // A partially typed closing fence is not code content.
        lines.push("");
      } else {
        lines.push(line);
      }
      continue;
    }
    const opening = line.match(/^ {0,3}(`{3,}|~{3,})(.*)$/);
    if (opening) {
      fence = { character: opening[1][0], length: opening[1].length };
      if (!plainText) lines.push(line);
      continue;
    }
    if (/^ {0,3}(?:#{1,6}|`{1,2}|~{1,2}|[*+-]|\d{1,9}[.)]|>)[ \t]*$/.test(line)) {
      lines.push("");
      continue;
    }
    if (/^ {0,3}(?:\*\s*){3,}$/.test(line)) {
      lines.push("");
      continue;
    }
    const heading = line.match(/^( {0,3})#{1,6}[ \t]+(.*)$/);
    const prefix = line.match(/^(\s*(?:>[ \t]+)?(?:[*+-]|\d{1,9}[.)])[ \t]+)(.*)$/);
    if (heading) {
      const text = heading[2].replace(/[ \t]+#+[ \t]*$/, "");
      lines.push(plainText ? cleanPlainLinks(cleanInlineMarkdown(text, true)) : `${line.slice(0, line.length - heading[2].length)}${cleanInlineMarkdown(text, false)}`);
    } else if (prefix) {
      const marker = plainText ? prefix[1].replace(/[*+-][ \t]+$/, "• ") : prefix[1];
      const text = cleanInlineMarkdown(prefix[2], plainText);
      lines.push(marker + (plainText ? cleanPlainLinks(text) : text));
    } else {
      const text = cleanInlineMarkdown(line, plainText);
      lines.push(plainText ? cleanPlainLinks(text.replace(/^ {0,3}>[ \t]?/, "")) : text);
    }
  }
  const result = lines.join("\n");
  return trim ? result.trim() : result;
}

export function getAssistantMarkdown(content: string, isTyping = false): string {
  return formatAssistantMarkdown(content, false, isTyping);
}

export function getAssistantPlainText(content: string, isTyping = false, trim = true): string {
  return formatAssistantMarkdown(content, true, isTyping, trim);
}
