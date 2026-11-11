import { describe, expect, it } from "vitest";

import { parseBlocks } from "./answer";

describe("parseBlocks", () => {
  it("separa parágrafos e listas", () => {
    expect(parseBlocks("Resumo:\n\n- leite R$ 4,29 [D1]\n- pão R$ 15,99\n\nTotal em outubro.")).toEqual([
      { kind: "p", lines: ["Resumo:"] },
      { kind: "ul", items: ["leite R$ 4,29 [D1]", "pão R$ 15,99"] },
      { kind: "p", lines: ["Total em outubro."] },
    ]);
  });

  it("lista numerada e quebras de linha no mesmo parágrafo", () => {
    expect(parseBlocks("Linha 1\nLinha 2\n1. um\n2) dois")).toEqual([
      { kind: "p", lines: ["Linha 1", "Linha 2"] },
      { kind: "ol", items: ["um", "dois"] },
    ]);
  });

  it("texto vazio", () => {
    expect(parseBlocks("\n\n")).toEqual([]);
  });
});

describe("tabelas", () => {
  it("vira bloco de tabela, sem a linha separadora", () => {
    const text = "Resumo:\n| Loja | Total |\n|---|---|\n| SENDAS | R$ 112,15 [D1] |\n| RCARVALHO | R$ 226,29 |\nFim.";

    expect(parseBlocks(text)).toEqual([
      { kind: "p", lines: ["Resumo:"] },
      { kind: "table", header: ["Loja", "Total"], rows: [["SENDAS", "R$ 112,15 [D1]"], ["RCARVALHO", "R$ 226,29"]] },
      { kind: "p", lines: ["Fim."] },
    ]);
  });
});
