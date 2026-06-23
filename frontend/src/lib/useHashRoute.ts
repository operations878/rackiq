import { useEffect, useState } from "react";

/**
 * Minimal hash-based router (no dependency). Routes are the bit after "#/", e.g.
 * "#/studio" -> "studio", "#/" or "" -> "". Refresh- and link-safe.
 */
export function useHashRoute(): [string, (to: string) => void] {
  const read = () => window.location.hash.replace(/^#\/?/, "");
  const [route, setRoute] = useState<string>(read());

  useEffect(() => {
    const onChange = () => setRoute(read());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);

  const navigate = (to: string) => {
    window.location.hash = `/${to}`;
  };

  return [route, navigate];
}
