/**
 * API (FastAPI) error messages in Portuguese.
 * Format: {"detail": "text"} or, on validation (422), {"detail": [{loc, msg, type}]}.
 */

const KNOWN_DETAILS: Record<string, string> = {
  "Not authenticated": "Sua sessão expirou. Entre novamente.",
  "Insufficient permissions": "Você não tem permissão para esta ação.",
  "Account not found": "Conta não encontrada.",
  "Slug already in use": "Já existe uma conta com esse identificador.",
  "User is already a member": "Essa pessoa já é membro da conta.",
  "Account must have at least one owner": "A conta precisa de pelo menos um dono.",
  "Category already exists": "Já existe uma categoria com esse nome.",
  "Category not found": "Categoria não encontrada.",
  "Document already uploaded": "Este documento já foi enviado.",
  "Email already linked to another login": "Este e-mail já está vinculado a outra conta Google.",
  "Google account email is not verified": "O e-mail da conta Google não está verificado.",
  "Email already allowed": "Este e-mail já está liberado.",
  "Access not found": "Liberação não encontrada.",
  "Only people granted by the platform admin can invite": "Só quem foi liberado pelo administrador pode convidar pessoas.",
  "Email not allowed": "Este e-mail ainda não tem acesso. Peça um convite a quem já usa o My Exp.",
  "Invalid Google token": "Não foi possível validar o login com o Google. Tente de novo.",
  "Too many requests, try again later": "Muitas tentativas. Aguarde um pouco e tente de novo.",
  "Request body too large": "Arquivo ou conteúdo grande demais.",
  "Google login is not configured": "Login com Google ainda não configurado.",
  "AI daily limit reached": "O limite diário da IA gratuita foi atingido.",
  "Document is being processed, try again in a moment": "O documento está sendo processado agora. Tente de novo em instantes.",
  "Document is already queued for processing": "Este documento já está na fila de processamento.",
};

const BY_STATUS: Record<number, string> = {
  400: "Requisição inválida.",
  401: "Sua sessão expirou. Entre novamente.",
  403: "Você não tem permissão para esta ação.",
  404: "Não encontrado.",
  409: "Conflito com dados existentes.",
  413: "Arquivo grande demais.",
  415: "Tipo de arquivo não suportado.",
  422: "Dados inválidos.",
  429: "Muitas tentativas. Aguarde um pouco e tente de novo.",
  503: "Serviço temporariamente indisponível. Tente novamente em instantes.",
};

const FALLBACK = "Algo deu errado. Tente novamente.";

type ValidationIssue = { loc?: unknown[] };

export function apiErrorMessage(body: unknown, status?: number): string {
  const detail = (body as { detail?: unknown } | null | undefined)?.detail;
  const byStatus = status ? BY_STATUS[status] : undefined;

  if (typeof detail === "string") {
    return KNOWN_DETAILS[detail] ?? byStatus ?? detail;
  }

  if (Array.isArray(detail) && detail.length > 0) {
    const field = (detail[0] as ValidationIssue).loc?.filter((part) => part !== "body").join(".");
    return field ? `Campo inválido: ${field}.` : BY_STATUS[422]!;
  }

  return byStatus ?? FALLBACK;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly body: unknown,
    /** Seconds from the Retry-After header (429/503), if present. */
    readonly retryAfter?: number,
  ) {
    super(apiErrorMessage(body, status));
    this.name = "ApiError";
  }
}

/** "às 21:00" (or "amanhã às 21:00"), in Brasília time, `seconds` from now. */
export function whenAvailable(seconds: number, now: Date = new Date()): string {
  const at = new Date(now.getTime() + seconds * 1000);
  const fmt = (d: Date, opts: Intl.DateTimeFormatOptions) =>
    new Intl.DateTimeFormat("pt-BR", { timeZone: "America/Sao_Paulo", ...opts }).format(d);
  const time = fmt(at, { hour: "2-digit", minute: "2-digit" });
  const sameDay = fmt(at, { dateStyle: "short" }) === fmt(now, { dateStyle: "short" });
  return sameDay ? `às ${time}` : `amanhã às ${time}`;
}
