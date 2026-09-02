export function isCaseTransitionPending(
  routeCaseId: string | null,
  activeCaseId: string | null,
  loadingCaseId: string | null,
): boolean {
  return loadingCaseId !== null || (routeCaseId !== null && activeCaseId !== routeCaseId);
}
