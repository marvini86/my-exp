/**
 * Account slug from the name: "Família Souza" → "familia-souza-k3f9".
 * The suffix avoids collisions (the slug is unique system-wide) without asking the user anything.
 */
export function slugify(name: string, suffix: string = randomSuffix()): string {
  const base = name
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 80)
    .replace(/-+$/g, "");
  return base ? `${base}-${suffix}` : `conta-${suffix}`;
}

function randomSuffix(): string {
  return Math.random().toString(36).slice(2, 6).padEnd(4, "0");
}
