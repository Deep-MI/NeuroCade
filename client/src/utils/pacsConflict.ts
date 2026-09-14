/** Only duplicate-study conflicts use the duplicate confirmation UI. */
export function duplicateStudyCases(body: unknown): string[] | null {
  if (!body || typeof body !== 'object' || !('detail' in body)) return null;
  const detail = body.detail;
  if (!detail || typeof detail !== 'object' || !('code' in detail) || detail.code !== 'duplicate_study') return null;
  if (!('case_ids' in detail) || !Array.isArray(detail.case_ids) || !detail.case_ids.every(id => typeof id === 'string')) return null;
  return detail.case_ids;
}
