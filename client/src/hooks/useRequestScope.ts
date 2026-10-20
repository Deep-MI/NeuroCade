import { useEffect, useRef } from 'react';
import { createRequestScope } from '../utils/requestScope';

export function useRequestScope(key: string) {
  const scopeRef = useRef<ReturnType<typeof createRequestScope> | null>(null);
  scopeRef.current ??= createRequestScope(key);
  const scope = scopeRef.current;
  scope.change(key);
  useEffect(() => {
    scope.activate();
    return () => scope.dispose();
  }, [scope]);
  return scope;
}
