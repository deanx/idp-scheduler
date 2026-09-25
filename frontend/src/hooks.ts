import { useCallback, useEffect, useState } from "react";

/** Hash routing, so the built app is one file served from any path. */
export function useRoute(): [string, (to: string) => void] {
  const [route, setRoute] = useState(() => window.location.hash.slice(1) || "/runs");
  useEffect(() => {
    const onChange = () => setRoute(window.location.hash.slice(1) || "/runs");
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  const navigate = useCallback((to: string) => {
    window.location.hash = to;
  }, []);
  return [route, navigate];
}

export interface Async<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

/** One fetch, with the three states every page here needs. */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]): Async<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let live = true;
    setLoading(true);
    setError(null);
    fn()
      .then((value) => live && setData(value))
      .catch((exc: Error) => live && setError(exc.message))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  return { data, error, loading, reload: () => setNonce((n) => n + 1) };
}
