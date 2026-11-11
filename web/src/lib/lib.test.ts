import { describe, expect, it, vi } from "vitest";

import { ACCESS_COOKIE, REFRESH_COOKIE, clearedSessionCookies, sessionCookies } from "./auth/cookies";
import { refreshSession } from "./auth/refresh";
import { ApiError, apiErrorMessage, whenAvailable } from "./errors";
import { formatBRL, formatDate, formatDateTime, formatMonth } from "./format";
import { slugify } from "./slug";

const TOKENS = { access_token: "mxa_a", refresh_token: "mxr_r", expires_in: 900, refresh_expires_in: 2592000 };

describe("cookies de sessão", () => {
  it("são httpOnly e o access vence 60 s antes do token", () => {
    const [access, refresh] = sessionCookies(TOKENS);

    expect(access).toMatchObject({ name: ACCESS_COOKIE, value: "mxa_a", options: { httpOnly: true, sameSite: "lax", maxAge: 840 } });
    expect(refresh).toMatchObject({ name: REFRESH_COOKIE, value: "mxr_r", options: { httpOnly: true, maxAge: 2592000 } });
  });

  it("limpeza zera os dois", () => {
    expect(clearedSessionCookies().map((c) => [c.name, c.value, c.options.maxAge])).toEqual([
      [ACCESS_COOKIE, "", 0],
      [REFRESH_COOKIE, "", 0],
    ]);
  });
});

describe("refreshSession", () => {
  const respond = (status: number, body: unknown = {}) =>
    vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }));

  it("200 devolve o par novo e envia o refresh no corpo", async () => {
    const fetchMock = respond(200, TOKENS);

    expect(await refreshSession("http://api", "mxr_old", fetchMock)).toEqual({ kind: "ok", tokens: TOKENS });
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("http://api/auth/refresh");
    expect(JSON.parse(init.body)).toEqual({ refresh_token: "mxr_old" });
  });

  it.each([
    [409, "rotated"],
    [401, "invalid"],
    [422, "invalid"],
    [503, "unavailable"],
  ])("HTTP %i → %s", async (status, kind) => {
    expect((await refreshSession("http://api", "x", respond(status))).kind).toBe(kind);
  });

  it("falha de rede não derruba a sessão", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("fetch failed"));

    expect((await refreshSession("http://api", "x", fetchMock)).kind).toBe("unavailable");
  });
});

describe("apiErrorMessage", () => {
  it("traduz mensagens conhecidas", () => {
    expect(apiErrorMessage({ detail: "Category already exists" }, 409)).toBe("Já existe uma categoria com esse nome.");
  });

  it("mensagem desconhecida usa o texto do status", () => {
    expect(apiErrorMessage({ detail: "Something new" }, 403)).toBe("Você não tem permissão para esta ação.");
  });

  it("422 do Pydantic aponta o campo", () => {
    expect(apiErrorMessage({ detail: [{ loc: ["body", "email"], msg: "x" }] }, 422)).toBe("Campo inválido: email.");
  });

  it("sem corpo", () => {
    expect(apiErrorMessage(null, 503)).toBe("Serviço temporariamente indisponível. Tente novamente em instantes.");
    expect(apiErrorMessage(undefined)).toBe("Algo deu errado. Tente novamente.");
  });

  it("ApiError carrega status e mensagem", () => {
    const error = new ApiError(429, { detail: "Too many requests, try again later" });

    expect([error.status, error.message]).toEqual([429, "Muitas tentativas. Aguarde um pouco e tente de novo."]);
  });
});

describe("formatação", () => {
  it("BRL a partir da string decimal da API", () => {
    expect(formatBRL("1234.5").replace(/\s/g, " ")).toBe("R$ 1.234,50");
    expect(formatBRL(null)).toBe("—");
    expect(formatBRL("abc")).toBe("—");
  });

  it("data sem hora não desloca o dia", () => {
    expect(formatDate("2026-10-05")).toBe("05/10/2026");
    expect(formatDate(null)).toBe("—");
  });

  it("data/hora no fuso de Brasília", () => {
    expect(formatDateTime("2026-11-07T01:30:00Z")).toBe("06/11/2026, 22:30");
  });

  it("mês", () => {
    expect(formatMonth("2026-10")).toMatch(/out/);
  });
});

describe("slugify", () => {
  it("remove acentos e símbolos e acrescenta sufixo", () => {
    expect(slugify("Família Souza & Cia!", "ab12")).toBe("familia-souza-cia-ab12");
  });

  it("nome sem letras", () => {
    expect(slugify("!!!", "zz99")).toBe("conta-zz99");
  });

  it("respeita o padrão da API", () => {
    expect(slugify("  Minha   Conta  ")).toMatch(/^[a-z0-9]+(?:-[a-z0-9]+)*$/);
  });
});

describe("whenAvailable", () => {
  it("mesmo dia em Brasília", () => {
    // 15:00 in Brasília (18:00 UTC) + 6 h = 21:00
    expect(whenAvailable(6 * 3600, new Date("2026-11-07T18:00:00Z"))).toBe("às 21:00");
  });

  it("vira o dia", () => {
    // 23:00 in Brasília + 2 h = 01:00 the next day
    expect(whenAvailable(2 * 3600, new Date("2026-11-08T02:00:00Z"))).toBe("amanhã às 01:00");
  });
});
