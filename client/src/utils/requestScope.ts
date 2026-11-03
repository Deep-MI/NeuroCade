/** A response may publish only into the scope that issued its request. */
export function createRequestScope(initialKey: string) {
  let key = initialKey;
  let generation = 0;
  let active = true;
  return {
    change(nextKey: string) {
      if (nextKey !== key) { key = nextKey; generation += 1; }
    },
    activate() { active = true; },
    dispose() { active = false; generation += 1; },
    capture() {
      const issuedGeneration = generation;
      return () => active && generation === issuedGeneration;
    },
  };
}
