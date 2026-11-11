/**
 * Formatting of the assistant's answers, with no injected HTML (no
 * dangerouslySetInnerHTML: the text comes from an LLM and from user documents).
 * Supports paragraphs, lists ("- ", "* ", "1. "), tables ("| a | b |"), **bold** and [D1] citations.
 */
import Link from "next/link";
import type { ReactNode } from "react";

export type CitationLink = { ref: string; href: string; label: string };

type Block =
  | { kind: "p"; lines: string[] }
  | { kind: "ul" | "ol"; items: string[] }
  | { kind: "table"; header: string[]; rows: string[][] };

const BULLET = /^\s*[-*•]\s+/;
const NUMBERED = /^\s*\d+[.)]\s+/;
const TABLE_ROW = /^\s*\|.*\|\s*$/;
const TABLE_SEPARATOR = /^\s*\|?[\s:|-]+\|?\s*$/;

function cells(line: string): string[] {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
}

export function parseBlocks(text: string): Block[] {
  const blocks: Block[] = [];
  for (const line of text.replace(/\r\n/g, "\n").split("\n")) {
    const last = blocks[blocks.length - 1];
    if (!line.trim()) {
      blocks.push({ kind: "p", lines: [] });
      continue;
    }
    if (TABLE_ROW.test(line)) {
      // A "|---|---|" line only separates the header; the table's 1st line is the header.
      if (TABLE_SEPARATOR.test(line)) continue;
      if (last?.kind === "table") last.rows.push(cells(line));
      else blocks.push({ kind: "table", header: cells(line), rows: [] });
      continue;
    }
    const kind = BULLET.test(line) ? "ul" : NUMBERED.test(line) ? "ol" : "p";
    if (kind === "p") {
      if (last?.kind === "p") last.lines.push(line.trim());
      else blocks.push({ kind: "p", lines: [line.trim()] });
    } else {
      const item = line.replace(kind === "ul" ? BULLET : NUMBERED, "").trim();
      if (last?.kind === kind) last.items.push(item);
      else blocks.push({ kind, items: [item] });
    }
  }
  return blocks.filter((b) => (b.kind === "p" ? b.lines.length > 0 : b.kind === "table" ? true : b.items.length > 0));
}

const INLINE = /(\*\*[^*]+\*\*|\[D\d+\])/g;

function inline(text: string, citations: Map<string, CitationLink>, keyPrefix: string): ReactNode[] {
  return text.split(INLINE).map((part, i) => {
    const key = `${keyPrefix}-${i}`;
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) return <strong key={key}>{part.slice(2, -2)}</strong>;
    const ref = /^\[(D\d+)\]$/.exec(part)?.[1];
    if (ref) {
      const citation = citations.get(ref);
      // Reference without a source (the API already removes invented ones): dropped from the text.
      if (!citation) return null;
      return (
        <Link
          key={key}
          href={citation.href}
          title={citation.label}
          className="mx-0.5 inline-flex -translate-y-px items-center rounded bg-violet-100 px-1 text-[11px] font-semibold text-violet-800 no-underline hover:bg-violet-200"
        >
          {ref.slice(1)}
        </Link>
      );
    }
    return part;
  });
}

export function AnswerText({ text, citations }: { text: string; citations: CitationLink[] }) {
  const byRef = new Map(citations.map((c) => [c.ref, c]));
  return (
    <div className="space-y-2 leading-relaxed">
      {parseBlocks(text).map((block, b) => {
        if (block.kind === "p") {
          return (
            <p key={b}>
              {block.lines.map((line, l) => (
                <span key={l}>
                  {l > 0 && <br />}
                  {inline(line, byRef, `${b}-${l}`)}
                </span>
              ))}
            </p>
          );
        }
        if (block.kind === "table") {
          return (
            <div key={b} className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-200 text-left text-slate-500">
                    {block.header.map((cell, c) => (
                      <th key={c} className="px-2 py-1.5 font-medium">{inline(cell, byRef, `${b}-h${c}`)}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {block.rows.map((row, r) => (
                    <tr key={r}>
                      {row.map((cell, c) => (
                        <td key={c} className="px-2 py-1.5 align-top tabular-nums">{inline(cell, byRef, `${b}-${r}-${c}`)}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        }
        const List = block.kind;
        return (
          <List key={b} className={List === "ul" ? "list-disc space-y-1 pl-5" : "list-decimal space-y-1 pl-5"}>
            {block.items.map((item, i) => (
              <li key={i}>{inline(item, byRef, `${b}-${i}`)}</li>
            ))}
          </List>
        );
      })}
    </div>
  );
}
