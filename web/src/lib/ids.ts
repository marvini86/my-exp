const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** IDs coming from the URL/form before they go to the API. */
export function isUuid(value: unknown): value is string {
  return typeof value === "string" && UUID.test(value);
}
