"use client";

// Keep one piece of page state in the URL (?claim=..., ?patient=...), so a view
// can be linked to and survives a refresh.
//
// WHY not Next's useSearchParams? It forces a <Suspense> boundary around every
// page that uses it. Reading window.location once on mount is enough here and
// keeps the pages simpler to read.
import { useCallback, useEffect, useState } from "react";

export function useQueryParam(name: string): [string, (v: string) => void] {
  const [value, setValue] = useState("");

  useEffect(() => {
    setValue(new URLSearchParams(window.location.search).get(name) ?? "");
  }, [name]);

  const update = useCallback(
    (v: string) => {
      setValue(v);
      const url = new URL(window.location.href);
      if (v) url.searchParams.set(name, v);
      else url.searchParams.delete(name);
      window.history.replaceState(null, "", url);
    },
    [name],
  );

  return [value, update];
}
